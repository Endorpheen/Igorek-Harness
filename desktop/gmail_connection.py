import sys
from factory.core.mcp_tasks import gmail as implementation
sys.modules[__name__] = implementation
