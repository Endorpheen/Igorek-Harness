import sys
from factory.core.mcp_tasks import access as implementation
sys.modules[__name__] = implementation
