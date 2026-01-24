"""
Omniparser Queue Service - Middleware for handling multiple SUT requests
Supports multiple Omniparser servers with load balancing (first-available routing).
Queues requests only when all servers are busy.
Prevents request denial when multiple SUTs send requests simultaneously.
"""

import asyncio
import time
import logging
import json
import os
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import requests
import uvicorn
from pydantic import BaseModel

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("omniparser_queue_service.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Configuration
OMNIPARSER_SERVER_URL = "http://localhost:8000"  # Target Omniparser server
REQUEST_TIMEOUT = 120  # Timeout for Omniparser requests in seconds
MAX_QUEUE_SIZE = 100  # Maximum number of requests in queue

# Request model
class ParseRequest(BaseModel):
    base64_image: str
    box_threshold: float = 0.05
    iou_threshold: float = 0.1
    use_paddleocr: bool = True

@dataclass
class QueuedRequest:
    """Represents a queued request with its metadata."""
    request_id: str
    payload: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)
    response_future: asyncio.Future = field(default_factory=lambda: asyncio.get_event_loop().create_future())


class ServerState(Enum):
    """Represents the current state of an Omniparser server."""
    IDLE = "idle"
    BUSY = "busy"
    UNHEALTHY = "unhealthy"


@dataclass
class ServerInfo:
    """Tracks state and statistics for a single Omniparser server."""
    url: str
    name: str
    state: ServerState = ServerState.IDLE
    current_request_id: Optional[str] = None
    last_health_check: Optional[datetime] = None
    consecutive_failures: int = 0

    # Per-server statistics
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_processing_time: float = 0.0
    last_request_time: Optional[datetime] = None


@dataclass
class LoadBalancerConfig:
    """Configuration for the load balancer."""
    servers: List[Dict[str, str]]
    request_timeout: int = 120
    health_check_interval: int = 30
    max_consecutive_failures: int = 3
    max_queue_size: int = 100
    recovery_check_interval: int = 10


def load_server_config(config_path: str) -> LoadBalancerConfig:
    """
    Load load balancer configuration from JSON file.

    Args:
        config_path: Path to the JSON configuration file

    Returns:
        LoadBalancerConfig object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid
    """
    with open(config_path, 'r', encoding='utf-8') as f:
        config_data = json.load(f)

    # Validate required fields
    if 'servers' not in config_data or not config_data['servers']:
        raise ValueError("Configuration must contain at least one server")

    for server in config_data['servers']:
        if 'url' not in server:
            raise ValueError("Each server must have a 'url' field")
        if 'name' not in server:
            server['name'] = server['url']  # Use URL as name if not specified

    settings = config_data.get('settings', {})

    return LoadBalancerConfig(
        servers=config_data['servers'],
        request_timeout=settings.get('request_timeout', 120),
        health_check_interval=settings.get('health_check_interval', 30),
        max_consecutive_failures=settings.get('max_consecutive_failures', 3),
        max_queue_size=settings.get('max_queue_size', 100),
        recovery_check_interval=settings.get('recovery_check_interval', 10)
    )


class ServerPoolManager:
    """
    Manages a pool of Omniparser servers with first-available routing.

    Responsibilities:
    - Track server states (idle/busy/unhealthy)
    - Route requests to first available server
    - Queue requests when all servers are busy
    - Perform background health checks for unhealthy servers
    - Collect per-server and aggregate statistics
    """

    def __init__(self, config: LoadBalancerConfig):
        self.config = config
        self.servers: Dict[str, ServerInfo] = {}
        self.server_order: List[str] = []  # Maintain order for first-available
        self.request_queue: asyncio.Queue = asyncio.Queue(maxsize=config.max_queue_size)
        self.server_locks: Dict[str, asyncio.Lock] = {}
        self._server_available_event: asyncio.Event = asyncio.Event()
        self._health_check_task: Optional[asyncio.Task] = None
        self._queue_processor_task: Optional[asyncio.Task] = None
        self.session = requests.Session()
        self._running = False

        # Initialize server info objects
        for server_config in config.servers:
            url = server_config['url']
            name = server_config.get('name', url)
            self.servers[url] = ServerInfo(url=url, name=name)
            self.server_order.append(url)
            self.server_locks[url] = asyncio.Lock()

        logger.info(f"ServerPoolManager initialized with {len(self.servers)} servers")

    async def initialize(self):
        """Initialize server pool and start background tasks."""
        self._running = True

        # Perform initial health check on all servers
        for url, server in self.servers.items():
            is_healthy = await self._check_server_health(server)
            if is_healthy:
                server.state = ServerState.IDLE
                server.last_health_check = datetime.now()
                logger.info(f"Server {server.name} is healthy")
            else:
                server.state = ServerState.UNHEALTHY
                server.last_health_check = datetime.now()
                logger.warning(f"Server {server.name} is unhealthy at startup")

        # Start background tasks
        self._health_check_task = asyncio.create_task(self._health_checker())
        self._queue_processor_task = asyncio.create_task(self._queue_processor())

        # Set event if any server is available
        if any(s.state == ServerState.IDLE for s in self.servers.values()):
            self._server_available_event.set()

        logger.info("ServerPoolManager initialized and background tasks started")

    async def shutdown(self):
        """Gracefully shutdown all background tasks."""
        self._running = False

        # Cancel background tasks
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        if self._queue_processor_task:
            self._queue_processor_task.cancel()
            try:
                await self._queue_processor_task
            except asyncio.CancelledError:
                pass

        logger.info("ServerPoolManager shutdown complete")

    def get_available_server(self) -> Optional[ServerInfo]:
        """
        Find first idle server using first-available strategy.
        Returns first IDLE server in config order.
        """
        for url in self.server_order:
            server = self.servers[url]
            if server.state == ServerState.IDLE:
                return server
        return None

    async def dispatch_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point for request handling.
        Routes to first available server or queues if all busy.
        """
        request_id = str(uuid.uuid4())[:8]

        # Check queue capacity
        if self.request_queue.qsize() >= self.config.max_queue_size:
            logger.warning(f"Queue full ({self.request_queue.qsize()}/{self.config.max_queue_size}), rejecting request")
            raise HTTPException(status_code=503, detail="Queue is full, please retry later")

        # Check if all servers are unhealthy
        healthy_servers = [s for s in self.servers.values() if s.state != ServerState.UNHEALTHY]
        if not healthy_servers:
            logger.error("All servers are unhealthy, rejecting request")
            raise HTTPException(status_code=503, detail="All servers are unavailable, please retry later")

        # Create queued request
        loop = asyncio.get_event_loop()
        queued_request = QueuedRequest(
            request_id=request_id,
            payload=payload,
            response_future=loop.create_future()
        )

        # Try to find an available server
        server = self.get_available_server()

        if server:
            # Server available - process immediately
            logger.info(f"Request {request_id} dispatched to {server.name}")
            asyncio.create_task(self._process_and_complete(server, queued_request))
        else:
            # All servers busy - queue the request
            logger.info(f"Request {request_id} queued (all servers busy, queue size: {self.request_queue.qsize() + 1})")
            await self.request_queue.put(queued_request)

        # Wait for result
        try:
            result = await queued_request.response_future
            return result
        except Exception as e:
            logger.error(f"Request {request_id} failed: {str(e)}")
            raise

    async def _process_and_complete(self, server: ServerInfo, queued_request: QueuedRequest):
        """Process request on server and handle completion/failure."""
        start_time = time.time()

        try:
            # Mark server busy
            async with self.server_locks[server.url]:
                server.state = ServerState.BUSY
                server.current_request_id = queued_request.request_id

            # Process the request
            result = await self._forward_to_server(server, queued_request)

            # Update success stats
            processing_time = time.time() - start_time
            async with self.server_locks[server.url]:
                server.successful_requests += 1
                server.consecutive_failures = 0
                server.total_processing_time += processing_time

            queued_request.response_future.set_result(result)
            logger.info(f"Request {queued_request.request_id} completed on {server.name} in {processing_time:.2f}s")

        except Exception as e:
            # Handle failure
            async with self.server_locks[server.url]:
                server.failed_requests += 1
                server.consecutive_failures += 1

                if server.consecutive_failures >= self.config.max_consecutive_failures:
                    server.state = ServerState.UNHEALTHY
                    logger.error(f"Server {server.name} marked UNHEALTHY after {server.consecutive_failures} consecutive failures")

            if not queued_request.response_future.done():
                queued_request.response_future.set_exception(e)
            logger.error(f"Request {queued_request.request_id} failed on {server.name}: {str(e)}")

        finally:
            # Mark server idle if still healthy
            async with self.server_locks[server.url]:
                server.total_requests += 1
                server.current_request_id = None
                server.last_request_time = datetime.now()

                if server.state == ServerState.BUSY:
                    server.state = ServerState.IDLE

            # Signal that a server is available
            self._server_available_event.set()

    async def _forward_to_server(self, server: ServerInfo, queued_request: QueuedRequest) -> Dict[str, Any]:
        """Forward a request to a specific Omniparser server."""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.session.post(
                    f"{server.url}/parse/",
                    json=queued_request.payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.config.request_timeout
                )
            )

            if response.status_code != 200:
                logger.error(f"Server {server.name} returned status {response.status_code}: {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Omniparser server error: {response.text}"
                )

            return response.json()

        except requests.Timeout:
            logger.error(f"Request {queued_request.request_id} timed out on {server.name}")
            raise HTTPException(status_code=504, detail=f"Request timed out on {server.name}")
        except requests.RequestException as e:
            logger.error(f"Request {queued_request.request_id} failed on {server.name}: {str(e)}")
            raise HTTPException(status_code=502, detail=f"Failed to connect to {server.name}: {str(e)}")

    async def _queue_processor(self):
        """Background task that dispatches queued requests when servers become available."""
        logger.info("Queue processor started")

        while self._running:
            try:
                # Wait for notification that a server is available
                await self._server_available_event.wait()
                self._server_available_event.clear()

                # Process queued requests while servers are available
                while not self.request_queue.empty():
                    server = self.get_available_server()
                    if not server:
                        break  # No more available servers

                    try:
                        queued_request = self.request_queue.get_nowait()
                        logger.info(f"Dequeued request {queued_request.request_id} for {server.name}")
                        asyncio.create_task(self._process_and_complete(server, queued_request))
                        self.request_queue.task_done()
                    except asyncio.QueueEmpty:
                        break

            except asyncio.CancelledError:
                logger.info("Queue processor cancelled")
                break
            except Exception as e:
                logger.error(f"Queue processor error: {e}")
                await asyncio.sleep(1)

    async def _health_checker(self):
        """Background task that periodically checks unhealthy servers for recovery."""
        logger.info("Health checker started")

        while self._running:
            try:
                await asyncio.sleep(self.config.recovery_check_interval)

                for server in self.servers.values():
                    if server.state == ServerState.UNHEALTHY:
                        is_healthy = await self._check_server_health(server)
                        if is_healthy:
                            async with self.server_locks[server.url]:
                                server.state = ServerState.IDLE
                                server.consecutive_failures = 0
                                server.last_health_check = datetime.now()
                            logger.info(f"Server {server.name} recovered, marking as IDLE")
                            self._server_available_event.set()
                        else:
                            server.last_health_check = datetime.now()

            except asyncio.CancelledError:
                logger.info("Health checker cancelled")
                break
            except Exception as e:
                logger.error(f"Health checker error: {e}")

    async def _check_server_health(self, server: ServerInfo) -> bool:
        """Perform health check on a single server."""
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.session.get(f"{server.url}/probe", timeout=5)
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.debug(f"Health check failed for {server.name}: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregate and per-server statistics."""
        # Calculate aggregate stats
        total_requests = sum(s.total_requests for s in self.servers.values())
        successful_requests = sum(s.successful_requests for s in self.servers.values())
        failed_requests = sum(s.failed_requests for s in self.servers.values())

        healthy_count = sum(1 for s in self.servers.values() if s.state != ServerState.UNHEALTHY)
        busy_count = sum(1 for s in self.servers.values() if s.state == ServerState.BUSY)
        idle_count = sum(1 for s in self.servers.values() if s.state == ServerState.IDLE)
        unhealthy_count = sum(1 for s in self.servers.values() if s.state == ServerState.UNHEALTHY)

        # Per-server stats
        per_server = {}
        for server in self.servers.values():
            avg_time = (server.total_processing_time / server.successful_requests
                       if server.successful_requests > 0 else 0)
            per_server[server.name] = {
                "url": server.url,
                "state": server.state.value,
                "current_request_id": server.current_request_id,
                "total_requests": server.total_requests,
                "successful_requests": server.successful_requests,
                "failed_requests": server.failed_requests,
                "average_processing_time": round(avg_time, 2),
                "consecutive_failures": server.consecutive_failures,
                "last_request_time": server.last_request_time.isoformat() if server.last_request_time else None,
                "last_health_check": server.last_health_check.isoformat() if server.last_health_check else None
            }

        return {
            "current_queue_size": self.request_queue.qsize(),
            "aggregate": {
                "total_requests": total_requests,
                "successful_requests": successful_requests,
                "failed_requests": failed_requests,
                "total_servers": len(self.servers),
                "healthy_servers": healthy_count,
                "busy_servers": busy_count,
                "idle_servers": idle_count,
                "unhealthy_servers": unhealthy_count
            },
            "per_server": per_server
        }

    def get_all_server_health(self) -> Dict[str, Any]:
        """Get health status of all servers for /probe endpoint."""
        servers_health = {}
        for server in self.servers.values():
            servers_health[server.name] = {
                "url": server.url,
                "status": "unhealthy" if server.state == ServerState.UNHEALTHY else "healthy",
                "state": server.state.value,
                "current_request": server.current_request_id,
                "last_check": server.last_health_check.isoformat() if server.last_health_check else None
            }
        return servers_health


class OmniparserQueueManager:
    """
    Manages request routing across Omniparser servers.

    Supports both single-server (legacy) and multi-server (load balancing) modes.
    Uses ServerPoolManager internally for all request handling.
    """

    def __init__(
        self,
        target_url: Optional[str] = None,
        config_path: Optional[str] = None,
        timeout: int = 120
    ):
        """
        Initialize the queue manager.

        Args:
            target_url: Single server URL (legacy mode)
            config_path: Path to multi-server JSON config (new mode)
            timeout: Request timeout in seconds

        One of target_url or config_path must be provided.
        If both provided, config_path takes precedence.
        """
        self.multi_server_mode = False
        self.config: Optional[LoadBalancerConfig] = None
        self.pool_manager: Optional[ServerPoolManager] = None

        if config_path:
            # Multi-server mode
            self.config = load_server_config(config_path)
            self.config.request_timeout = timeout
            self.pool_manager = ServerPoolManager(self.config)
            self.multi_server_mode = True
            logger.info(f"OmniparserQueueManager initialized with {len(self.config.servers)} servers from {config_path}")
        elif target_url:
            # Legacy single-server mode (backward compatible)
            self.config = LoadBalancerConfig(
                servers=[{"name": "default", "url": target_url}],
                request_timeout=timeout
            )
            self.pool_manager = ServerPoolManager(self.config)
            self.multi_server_mode = False
            logger.info(f"OmniparserQueueManager initialized in single-server mode: {target_url}")
        else:
            # Deferred initialization - will be set up later via properties
            logger.info("OmniparserQueueManager created with deferred initialization")

    def _ensure_initialized(self, target_url: str, timeout: int = 120):
        """Initialize pool manager if not already done (for legacy compatibility)."""
        if self.pool_manager is None:
            self.config = LoadBalancerConfig(
                servers=[{"name": "default", "url": target_url}],
                request_timeout=timeout
            )
            self.pool_manager = ServerPoolManager(self.config)
            self.multi_server_mode = False
            logger.info(f"OmniparserQueueManager late-initialized with: {target_url}")

    @property
    def target_url(self) -> str:
        """Get target URL (for backward compatibility)."""
        if self.config and self.config.servers:
            return self.config.servers[0]['url']
        return OMNIPARSER_SERVER_URL

    @target_url.setter
    def target_url(self, value: str):
        """Set target URL (for backward compatibility with legacy code)."""
        if self.pool_manager is None:
            self._ensure_initialized(value)
        else:
            # Update first server URL if in single-server mode
            if not self.multi_server_mode and self.config:
                self.config.servers[0]['url'] = value
                if self.pool_manager and self.pool_manager.servers:
                    first_url = self.pool_manager.server_order[0]
                    self.pool_manager.servers[first_url].url = value

    @property
    def timeout(self) -> int:
        """Get timeout (for backward compatibility)."""
        return self.config.request_timeout if self.config else REQUEST_TIMEOUT

    @timeout.setter
    def timeout(self, value: int):
        """Set timeout (for backward compatibility)."""
        if self.config:
            self.config.request_timeout = value

    async def start_worker(self):
        """Start the pool manager and background tasks."""
        if self.pool_manager:
            await self.pool_manager.initialize()
        logger.info("Queue manager started")

    async def stop_worker(self):
        """Stop the pool manager and background tasks."""
        if self.pool_manager:
            await self.pool_manager.shutdown()
        logger.info("Queue manager stopped")

    async def enqueue_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a request through the server pool.
        Routes to first available server or queues if all busy.

        Args:
            payload: Request payload for Omniparser

        Returns:
            Response from Omniparser server
        """
        if self.pool_manager:
            return await self.pool_manager.dispatch_request(payload)
        raise HTTPException(status_code=500, detail="Queue manager not initialized")

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregate and per-server statistics."""
        if self.pool_manager:
            return self.pool_manager.get_stats()
        return {
            "current_queue_size": 0,
            "aggregate": {
                "total_requests": 0,
                "successful_requests": 0,
                "failed_requests": 0
            }
        }

    async def health_check(self) -> Dict[str, Any]:
        """Get health status of all servers."""
        if self.pool_manager:
            return self.pool_manager.get_all_server_health()
        return {"status": "not_initialized"}

# Global queue manager instance (will be initialized in main() or with defaults)
queue_manager: Optional[OmniparserQueueManager] = None

# Environment variable names for configuration
ENV_CONFIG_PATH = "OMNIPARSER_CONFIG_PATH"
ENV_SERVER_URL = "OMNIPARSER_SERVER_URL"
ENV_TIMEOUT = "OMNIPARSER_TIMEOUT"


def get_queue_manager() -> OmniparserQueueManager:
    """Get or create the global queue manager based on environment variables."""
    global queue_manager
    if queue_manager is None:
        # Check environment variables for configuration
        config_path = os.environ.get(ENV_CONFIG_PATH)
        server_url = os.environ.get(ENV_SERVER_URL, OMNIPARSER_SERVER_URL)
        timeout = int(os.environ.get(ENV_TIMEOUT, REQUEST_TIMEOUT))

        if config_path:
            # Multi-server mode from config file
            logger.info(f"Initializing from config file: {config_path}")
            queue_manager = OmniparserQueueManager(
                config_path=config_path,
                timeout=timeout
            )
        else:
            # Single-server mode
            logger.info(f"Initializing with single server: {server_url}")
            queue_manager = OmniparserQueueManager(
                target_url=server_url,
                timeout=timeout
            )
    return queue_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    qm = get_queue_manager()
    logger.info("Starting Omniparser Queue Service")

    if qm.multi_server_mode:
        logger.info(f"Mode: Multi-server load balancing with {len(qm.config.servers)} servers")
        for server in qm.config.servers:
            logger.info(f"  - {server.get('name', server['url'])}: {server['url']}")
    else:
        logger.info(f"Mode: Single-server")
        logger.info(f"Target Omniparser server: {qm.target_url}")

    logger.info(f"Request timeout: {qm.timeout}s")
    logger.info(f"Max queue size: {qm.config.max_queue_size if qm.config else MAX_QUEUE_SIZE}")

    await qm.start_worker()

    yield

    # Shutdown
    logger.info("Shutting down Omniparser Queue Service")
    await qm.stop_worker()


# FastAPI app with lifespan
app = FastAPI(
    title="Omniparser Queue Service",
    version="2.0.0",
    lifespan=lifespan
)

@app.get("/probe")
async def probe():
    """
    Health check endpoint - compatible with OmniparserClient.
    Returns queue service health and all Omniparser server health status.
    """
    qm = get_queue_manager()
    health_status = await qm.health_check()
    stats = qm.get_stats()

    return {
        "service": "omniparser_queue_service",
        "version": "2.0.0",
        "mode": "multi-server" if qm.multi_server_mode else "single-server",
        "queue_service_status": "running",
        "servers": health_status,
        "aggregate_stats": stats.get("aggregate", {}),
        "queue_size": stats.get("current_queue_size", 0)
    }


@app.post("/parse/")
async def parse_image(request: ParseRequest):
    """
    Parse image endpoint - routes request through load balancer to Omniparser.
    Compatible with OmniparserClient API format.

    Args:
        request: Parse request with base64_image and parameters

    Returns:
        Omniparser response with parsed_content_list and som_image_base64
    """
    qm = get_queue_manager()
    try:
        # Convert request to dict
        payload = request.dict()

        # Enqueue and wait for result
        logger.info(f"Received parse request (image size: {len(payload['base64_image'])} bytes)")
        result = await qm.enqueue_request(payload)

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Parse request failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/stats")
async def get_stats():
    """Get detailed queue and server statistics."""
    qm = get_queue_manager()
    return qm.get_stats()


@app.get("/")
async def root():
    """Root endpoint with service information."""
    qm = get_queue_manager()
    server_count = len(qm.config.servers) if qm.config else 1

    return {
        "service": "Omniparser Queue Service",
        "version": "2.0.0",
        "mode": "multi-server" if qm.multi_server_mode else "single-server",
        "server_count": server_count,
        "endpoints": {
            "parse": "/parse/",
            "health": "/probe",
            "stats": "/stats"
        }
    }

def main():
    """Run the queue service."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Omniparser Queue Service with Load Balancing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single-server mode (legacy)
  python omniparser_queue_service.py --omniparser-url http://localhost:8000

  # Multi-server mode with config file
  python omniparser_queue_service.py --config omniparser_servers.json

  # Custom port
  python omniparser_queue_service.py --config omniparser_servers.json --port 9001
        """
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9000,
        help="Port to run the service on (default: 9000)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--omniparser-url",
        type=str,
        default=None,
        help="Single Omniparser server URL (legacy mode)"
    )
    parser.add_argument(
        "--config",
        type=str,
        default="omniparser_servers.json",
        help="Path to JSON config file with multiple servers (default: omniparser_servers.json)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=REQUEST_TIMEOUT,
        help=f"Request timeout in seconds (default: {REQUEST_TIMEOUT})"
    )

    args = parser.parse_args()

    # Set environment variables for configuration (uvicorn will use these)
    os.environ[ENV_TIMEOUT] = str(args.timeout)

    if args.omniparser_url:
        # Single-server mode (explicit URL) - takes priority
        os.environ[ENV_SERVER_URL] = args.omniparser_url
        # Clear config path if set
        if ENV_CONFIG_PATH in os.environ:
            del os.environ[ENV_CONFIG_PATH]
        logger.info(f"Starting in single-server mode: {args.omniparser_url}")
    elif args.config and os.path.exists(args.config):
        # Multi-server mode from config file
        config_path = os.path.abspath(args.config)
        os.environ[ENV_CONFIG_PATH] = config_path
        logger.info(f"Starting in multi-server mode from config: {config_path}")
    else:
        # Default single-server mode (config file not found or not specified)
        if args.config and not os.path.exists(args.config):
            logger.warning(f"Config file not found: {args.config}, falling back to single-server mode")
        os.environ[ENV_SERVER_URL] = OMNIPARSER_SERVER_URL
        # Clear config path if set
        if ENV_CONFIG_PATH in os.environ:
            del os.environ[ENV_CONFIG_PATH]
        logger.info(f"Starting with default server: {OMNIPARSER_SERVER_URL}")

    logger.info(f"Starting server on {args.host}:{args.port}")

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info"
    )


if __name__ == "__main__":
    main()
