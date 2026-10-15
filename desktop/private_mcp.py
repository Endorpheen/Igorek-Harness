import sys
from factory.core.mcp_tasks import private as implementation
sys.modules[__name__] = implementation
