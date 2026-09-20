import unittest
from unittest.mock import patch

import mcp_stdio


class MCPStdioTest(unittest.TestCase):
    def test_forward_timeout_exceeds_owned_sbc_solver_window(self):
        with patch("mcp_stdio.urlopen", side_effect=TimeoutError) as mocked:
            with self.assertRaisesRegex(RuntimeError, "fc27d is unavailable"):
                mcp_stdio.forward({"jsonrpc": "2.0", "id": 1, "method": "ping"})

        self.assertEqual(mocked.call_args.kwargs["timeout"], 240)


if __name__ == "__main__":
    unittest.main()
