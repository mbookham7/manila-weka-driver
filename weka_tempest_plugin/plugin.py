import os

from tempest.test_discover import plugins


class WekaFSTempestPlugin(plugins.TempestPlugin):
    """Tempest plugin for the Manila Weka driver — WEKAFS protocol tests."""

    def load_tests(self):
        base_path = os.path.split(os.path.dirname(
            os.path.abspath(__file__)))[0]
        test_dir = "weka_tempest_plugin/tests"
        full_test_dir = os.path.join(base_path, test_dir)
        return full_test_dir, base_path

    def register_opts(self, conf):
        # Re-use Manila tempest plugin config — no new options needed.
        pass

    def get_opt_lists(self):
        return []

    def get_service_clients(self):
        return []
