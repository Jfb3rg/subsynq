"""Execute the installer retry shell with local stubs, without network or rentals."""
import subprocess
import sys
import unittest

sys.path.insert(0, '/app')
import cloud_gpu


class OllamaInstallTests(unittest.TestCase):
    def run_install(self, download_fails=False, installer_failures=0):
        script = ('set -e -o pipefail\n'
                  'downloads=0; installs=0\n'
                  'sleep() { :; }\n'
                  'curl() { downloads=$((downloads+1)); '
                  + ('return 22; ' if download_fails else 'printf "installer" > "${@: -1}"; ')
                  + '}\n'
                  'sh() { installs=$((installs+1)); '
                  + f'[ "$installs" -gt {installer_failures} ]; '
                  + '}\n'
                  + cloud_gpu.CHAIN_OLLAMA_INSTALL_SCRIPT
                  + 'if install_ollama; then echo READY; else echo FAILED; fi\n'
                  + 'echo "calls=$downloads/$installs"\n')
        result = subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_failed_download_does_not_execute_a_partial_installer(self):
        output = self.run_install(download_fails=True)
        self.assertIn('FAILED', output)
        self.assertIn('calls=3/0', output)

    def test_failure_inside_the_downloaded_installer_is_retried(self):
        output = self.run_install(installer_failures=1)
        self.assertIn('READY', output)
        self.assertIn('calls=2/2', output)

    def test_permanent_installer_failure_stops_after_three_attempts(self):
        output = self.run_install(installer_failures=9)
        self.assertIn('FAILED', output)
        self.assertIn('calls=3/3', output)
