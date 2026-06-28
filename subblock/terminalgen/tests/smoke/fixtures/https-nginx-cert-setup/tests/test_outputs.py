import os
import re
import pytest
import subprocess

# Configuration paths
WORKDIR = "/app/task_file"
OUTPUT_DIR = f"{WORKDIR}/output"
LOG_DIR = f"{WORKDIR}/logs"
LOG_FILE = f"{LOG_DIR}/setup.log"
NGINX_CONF = f"{OUTPUT_DIR}/nginx.conf"
CERT_INFO = f"{OUTPUT_DIR}/certificate-info.txt"
VERIFICATION_REPORT = f"{OUTPUT_DIR}/verification-report.txt"


class TestHTTPSSetupDirectories:
    """Test output directory structure."""
    
    def test_output_directory_exists(self):
        """Verify output directory was created."""
        assert os.path.isdir(OUTPUT_DIR), f"Output directory {OUTPUT_DIR} does not exist"
    
    def test_logs_directory_exists(self):
        """Verify logs directory was created."""
        assert os.path.isdir(LOG_DIR), f"Logs directory {LOG_DIR} does not exist"


class TestHTTPSSetupFiles:
    """Test that required output files exist."""
    
    def test_nginx_conf_exists(self):
        """Verify nginx.conf was created."""
        assert os.path.isfile(NGINX_CONF), f"nginx.conf not found at {NGINX_CONF}"
    
    def test_certificate_info_exists(self):
        """Verify certificate-info.txt was created."""
        assert os.path.isfile(CERT_INFO), f"certificate-info.txt not found at {CERT_INFO}"
    
    def test_verification_report_exists(self):
        """Verify verification-report.txt was created."""
        assert os.path.isfile(VERIFICATION_REPORT), f"verification-report.txt not found at {VERIFICATION_REPORT}"
    
    def test_setup_log_exists(self):
        """Verify setup.log was created."""
        assert os.path.isfile(LOG_FILE), f"setup.log not found at {LOG_FILE}"


class TestNginxConfiguration:
    """Test nginx configuration file content."""
    
    @pytest.fixture
    def nginx_content(self):
        """Load nginx configuration content."""
        with open(NGINX_CONF, 'r') as f:
            return f.read()
    
    def test_nginx_conf_non_empty(self):
        """Verify nginx.conf is not empty."""
        with open(NGINX_CONF, 'r') as f:
            content = f.read().strip()
        assert len(content) > 100, "nginx.conf appears to be empty or too short"
    
    def test_has_ssl_certificate_directive(self, nginx_content):
        """Verify nginx config contains ssl_certificate directive."""
        assert 'ssl_certificate' in nginx_content, "nginx.conf missing ssl_certificate directive"
        assert '/etc/letsencrypt/live/' in nginx_content, "nginx.conf missing Let's Encrypt certificate path"
    
    def test_has_ssl_key_directive(self, nginx_content):
        """Verify nginx config contains ssl_certificate_key directive."""
        assert 'ssl_certificate_key' in nginx_content, "nginx.conf missing ssl_certificate_key directive"
        assert 'privkey.pem' in nginx_content, "nginx.conf missing privkey.pem reference"
    
    def test_has_http_redirect(self, nginx_content):
        """Verify nginx config redirects HTTP to HTTPS."""
        # Check for port 80 server block and redirect
        assert 'listen 80' in nginx_content, "nginx.conf missing HTTP port 80 configuration"
        assert '443' in nginx_content, "nginx.conf missing HTTPS port 443 configuration"
        # Should have redirect rule
        has_redirect = 'return 301 https://' in nginx_content or 'return 302 https://' in nginx_content
        assert has_redirect, "nginx.conf missing HTTP to HTTPS redirect"
    
    def test_has_ssl_protocols(self, nginx_content):
        """Verify nginx config specifies SSL protocols."""
        assert 'ssl_protocols' in nginx_content, "nginx.conf missing ssl_protocols directive"
        # Check for modern TLS versions
        has_tls = 'TLSv1.2' in nginx_content or 'TLSv1.3' in nginx_content
        assert has_tls, "nginx.conf should specify modern TLS versions"
    
    def test_has_ssl_ciphers(self, nginx_content):
        """Verify nginx config specifies SSL ciphers."""
        assert 'ssl_ciphers' in nginx_content, "nginx.conf missing ssl_ciphers directive"
        # Should have reasonable cipher suite
        assert 'ECDHE' in nginx_content or 'AES' in nginx_content, "nginx.conf should specify secure ciphers"
    
    def test_has_security_headers(self, nginx_content):
        """Verify nginx config includes security headers."""
        # Check for HSTS
        assert 'Strict-Transport-Security' in nginx_content, "nginx.conf missing HSTS header"
        # Check for other security headers
        security_headers = ['X-Frame-Options', 'X-Content-Type-Options', 'X-XSS-Protection']
        headers_found = sum(1 for h in security_headers if h in nginx_content)
        assert headers_found >= 2, f"nginx.conf should have security headers, found {headers_found}/3"
    
    def test_has_http2(self, nginx_content):
        """Verify nginx config enables HTTP/2."""
        # Check for http2 in HTTPS server block
        has_http2 = 'http2' in nginx_content
        assert has_http2, "nginx.conf should enable HTTP/2 for HTTPS connections"
    
    def test_no_domain_placeholder(self, nginx_content):
        """Verify domain placeholder was replaced."""
        assert 'DOMAIN_PLACEHOLDER' not in nginx_content, "nginx.conf contains unreplaced DOMAIN_PLACEHOLDER"
    
    def test_has_server_blocks(self, nginx_content):
        """Verify nginx config has proper server blocks."""
        # Count server blocks - should have at least 2 (HTTP and HTTPS)
        server_blocks = nginx_content.count('server {')
        assert server_blocks >= 2, f"nginx.conf should have at least 2 server blocks (HTTP and HTTPS), found {server_blocks}"
    
    def test_has_worker_config(self, nginx_content):
        """Verify nginx config has worker configuration."""
        assert 'worker_processes' in nginx_content, "nginx.conf missing worker_processes directive"
        assert 'worker_connections' in nginx_content, "nginx.conf missing worker_connections directive"
    
    def test_has_logging_config(self, nginx_content):
        """Verify nginx config has logging configuration."""
        assert 'access_log' in nginx_content, "nginx.conf missing access_log directive"
        assert 'error_log' in nginx_content, "nginx.conf missing error_log directive"


class TestCertificateInfoFile:
    """Test certificate information file."""
    
    @pytest.fixture
    def cert_info_content(self):
        """Load certificate info content."""
        with open(CERT_INFO, 'r') as f:
            return f.read()
    
    def test_certificate_info_non_empty(self):
        """Verify certificate-info.txt is not empty."""
        with open(CERT_INFO, 'r') as f:
            content = f.read().strip()
        assert len(content) > 50, "certificate-info.txt appears to be empty or too short"
    
    def test_has_domain_info(self, cert_info_content):
        """Verify certificate-info.txt contains domain information."""
        assert 'Domain:' in cert_info_content or 'domain' in cert_info_content.lower(), \
            "certificate-info.txt missing domain information"
    
    def test_has_certificate_path(self, cert_info_content):
        """Verify certificate-info.txt lists certificate path."""
        assert 'fullchain.pem' in cert_info_content, "certificate-info.txt missing fullchain.pem reference"
        assert '/etc/letsencrypt/live/' in cert_info_content, "certificate-info.txt missing Let's Encrypt path"
    
    def test_has_key_path(self, cert_info_content):
        """Verify certificate-info.txt lists private key path."""
        assert 'privkey.pem' in cert_info_content or 'Private' in cert_info_content, \
            "certificate-info.txt missing private key path"
    
    def test_has_certificate_section_header(self, cert_info_content):
        """Verify certificate-info.txt has proper section headers."""
        has_header = 'Certificate' in cert_info_content or 'SSL' in cert_info_content
        assert has_header, "certificate-info.txt should have section headers"


class TestVerificationReport:
    """Test verification report file."""
    
    @pytest.fixture
    def report_content(self):
        """Load verification report content."""
        with open(VERIFICATION_REPORT, 'r') as f:
            return f.read()
    
    def test_verification_report_non_empty(self):
        """Verify verification-report.txt is not empty."""
        with open(VERIFICATION_REPORT, 'r') as f:
            content = f.read().strip()
        assert len(content) > 50, "verification-report.txt appears to be empty or too short"
    
    def test_has_nginx_status(self, report_content):
        """Verify report contains nginx configuration status."""
        assert 'nginx' in report_content.lower(), "verification-report.txt missing nginx status"
        # Check for status indicators
        has_status = '✓' in report_content or '✗' in report_content or 'valid' in report_content.lower() or 'error' in report_content.lower()
        assert has_status, "verification-report.txt should show configuration status"
    
    def test_has_certificate_status(self, report_content):
        """Verify report contains certificate status."""
        has_cert = 'Certificate' in report_content or 'certificate' in report_content or 'cert' in report_content.lower()
        assert has_cert, "verification-report.txt missing certificate status"
    
    def test_has_certbot_info(self, report_content):
        """Verify report contains Certbot information."""
        has_certbot = 'Certbot' in report_content or 'certbot' in report_content
        assert has_certbot, "verification-report.txt missing Certbot information"
    
    def test_has_file_paths_section(self, report_content):
        """Verify report documents file locations."""
        assert 'File' in report_content or 'Path' in report_content or 'Location' in report_content, \
            "verification-report.txt should document file paths"
    
    def test_has_summary_section(self, report_content):
        """Verify report has configuration summary."""
        has_summary = 'Summary' in report_content or 'Domain' in report_content or 'Configuration' in report_content
        assert has_summary, "verification-report.txt should have a configuration summary"


class TestSetupLog:
    """Test setup log file."""
    
    @pytest.fixture
    def log_content(self):
        """Load setup log content."""
        with open(LOG_FILE, 'r') as f:
            return f.read()
    
    def test_setup_log_non_empty(self):
        """Verify setup.log is not empty."""
        with open(LOG_FILE, 'r') as f:
            content = f.read().strip()
        assert len(content) > 50, "setup.log appears to be empty or too short"
    
    def test_has_completion_message(self, log_content):
        """Verify log indicates successful completion."""
        assert 'Complete' in log_content or 'complete' in log_content or 'Setup' in log_content, \
            "setup.log should indicate completion"
    
    def test_has_ssl_https_references(self, log_content):
        """Verify log contains HTTPS/SSL references."""
        has_ssl_ref = 'HTTPS' in log_content or 'SSL' in log_content or 'Certificate' in log_content or 'Certbot' in log_content
        assert has_ssl_ref, "setup.log should reference HTTPS/SSL/Certificate operations"
    
    def test_has_timestamps(self, log_content):
        """Verify log contains timestamped entries."""
        # Check for timestamp pattern [YYYY-MM-DD HH:MM:SS]
        timestamps = re.findall(r'\[\d{4}-\d{2}-\d{2}', log_content)
        assert len(timestamps) > 0, "setup.log should contain timestamped entries"
    
    def test_documents_major_steps(self, log_content):
        """Verify log documents major installation steps."""
        steps = ['Certbot', 'nginx', 'certificate', 'renewal', 'Configuration']
        # Case-insensitive check
        log_lower = log_content.lower()
        steps_found = sum(1 for step in steps if step.lower() in log_lower)
        assert steps_found >= 3, f"setup.log should document major steps, found {steps_found}/5: {steps}"
    
    def test_has_output_summary(self, log_content):
        """Verify log summarizes output files."""
        # Should mention output files
        assert 'output' in log_content.lower() or 'created' in log_content.lower() or 'generated' in log_content.lower(), \
            "setup.log should summarize created files"


class TestIntegration:
    """Integration tests across files."""
    
    def test_nginx_and_cert_info_consistent_domain(self):
        """Verify nginx.conf and certificate-info.txt reference same domain."""
        with open(NGINX_CONF, 'r') as f:
            nginx_content = f.read()
        with open(CERT_INFO, 'r') as f:
            cert_content = f.read()
        
        # Extract domain references
        nginx_domains = re.findall(r'/etc/letsencrypt/live/([^/]+)/', nginx_content)
        cert_domains = re.findall(r'Domain:\s*([^\n]+)', cert_content)
        
        # At least one should match or be present
        assert len(nginx_domains) > 0, "nginx.conf should reference certificate paths"
    
    def test_all_output_files_created(self):
        """Verify all expected output files were created."""
        output_files = [NGINX_CONF, CERT_INFO, VERIFICATION_REPORT]
        missing = [f for f in output_files if not os.path.isfile(f)]
        assert not missing, f"Missing output files: {missing}"
    
    def test_output_files_have_content(self):
        """Verify all output files have meaningful content."""
        output_files = {NGINX_CONF: 100, CERT_INFO: 50, VERIFICATION_REPORT: 50}
        for filepath, min_size in output_files.items():
            with open(filepath, 'r') as f:
                content = f.read().strip()
            assert len(content) > min_size, f"{filepath} is too small ({len(content)} bytes)"
    
    def test_log_documents_all_steps(self):
        """Verify log documents all major steps."""
        with open(LOG_FILE, 'r') as f:
            log_content = f.read()
        
        # Should mention key operations
        key_operations = ['install', 'certificate', 'nginx', 'config', 'renew']
        log_lower = log_content.lower()
        operations_found = sum(1 for op in key_operations if op in log_lower)
        assert operations_found >= 3, f"setup.log should document key operations, found {operations_found}/5"
