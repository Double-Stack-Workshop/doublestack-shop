import json
import unittest
from unittest.mock import Mock, patch

from backend.app import services


class ContainerPortTests(unittest.TestCase):
    def test_container_list_collapses_dual_stack_bindings_and_keeps_multiple_ports(self):
        output = (
            'abc123|demo|nginx:latest|Up 2 hours|'
            '0.0.0.0:8080->80/tcp, [::]:8080->80/tcp, '
            '0.0.0.0:8443->443/tcp, [::]:8443->443/tcp|now|nginx\n'
        )
        result = Mock(returncode=0, stdout=output)
        with patch.object(services.subprocess, 'run', return_value=result):
            containers = services.get_all_containers()
        self.assertEqual(containers[0]['ports'], ['8080->80/tcp', '8443->443/tcp'])

    def test_container_detail_collapses_duplicate_bindings_and_preserves_protocols(self):
        inspected = [{
            'Id': 'abc123', 'Name': '/demo', 'Created': '2026-09-01T00:00:00Z',
            'Config': {'Image': 'nginx:latest', 'Cmd': ['nginx']},
            'State': {'Status': 'exited', 'StartedAt': ''},
            'NetworkSettings': {'Ports': {
                '80/tcp': [
                    {'HostIp': '0.0.0.0', 'HostPort': '8080'},
                    {'HostIp': '::', 'HostPort': '8080'},
                ],
                '53/udp': [{'HostIp': '0.0.0.0', 'HostPort': '5353'}],
            }},
        }]
        result = Mock(returncode=0, stdout=json.dumps(inspected))
        with patch.object(services.subprocess, 'run', return_value=result):
            container = services.get_container_by_id('abc123')
        self.assertEqual(container['ports'], ['8080->80/tcp', '5353->53/udp'])


if __name__ == '__main__':
    unittest.main()
