# HTTPS Certificate (SSL) Configuration with Nginx

## Task Overview
Configure a trusted SSL/TLS certificate for an nginx web server so that users can access your website securely without browser warnings. This task involves obtaining a browser-trusted certificate using Let's Encrypt and Certbot, then configuring nginx to serve HTTPS traffic.

## Prerequisites
- Linux operating system with terminal access
- nginx installed and running
- Root or sudo access to the system
- A registered domain name (required for certificate validation)
- Port 80 and 443 accessible from the internet

## Task Description

### Objective
Set up HTTPS with a valid, browser-trusted SSL certificate on an nginx web server so that:
- Users can access the site via HTTPS without certificate warnings
- The certificate is automatically renewed before expiration
- HTTP traffic is redirected to HTTPS

### Step-by-Step Implementation

1. **Install Certbot and nginx plugin**
   - Use the appropriate package manager for your Linux distribution
   - Install certbot and the certbot-nginx plugin

2. **Obtain the Certificate**
   - Use Certbot to request a certificate from Let's Encrypt
   - Authenticate domain ownership using the HTTP-01 challenge method
   - Certificate will be automatically placed in `/etc/letsencrypt/live/`

3. **Configure nginx**
   - Update the nginx configuration file (located at `/app/task_file/input/nginx.conf`) to:
     - Enable SSL/TLS with the certificate and private key
     - Configure secure cipher suites and protocols
     - Redirect HTTP (port 80) to HTTPS (port 443)
   - Place the updated configuration at `/app/task_file/output/nginx.conf`

4. **Enable Auto-Renewal**
   - Configure Certbot to automatically renew the certificate before expiration
   - Verify the renewal configuration works correctly

5. **Test and Verify**
   - Restart nginx with the new configuration
   - Test HTTPS connectivity
   - Verify certificate validity in a browser or using command-line tools

## Working Directory Structure
```
/app/task_file/
├── input/
│   └── nginx.conf          # Original nginx configuration file
├── output/
│   ├── nginx.conf          # Modified nginx configuration with SSL settings
│   ├── certificate-info.txt # Certificate details and paths
│   └── verification-report.txt # Test results and verification output
└── logs/
    └── setup.log           # Log of all operations performed
```

## Input Files
- **`/app/task_file/input/nginx.conf`** - Base nginx configuration file that needs SSL/TLS setup

## Output Files
- **`/app/task_file/output/nginx.conf`** - Updated nginx configuration with:
  - SSL certificate path pointing to `/etc/letsencrypt/live/your-domain/`
  - SSL private key path
  - Proper SSL directives and cipher configurations
  - HTTP to HTTPS redirect rules
  
- **`/app/task_file/output/certificate-info.txt`** - Contains:
  - Certificate file location
  - Private key file location
  - Expiration date
  - Domain(s) covered
  
- **`/app/task_file/output/verification-report.txt`** - Contains:
  - OpenSSL certificate verification output
  - nginx configuration syntax check results
  - HTTPS connectivity test results
  - Certificate chain validation results

## Success Criteria

1. ✅ **Certificate Obtained**
   - A valid certificate from Let's Encrypt is obtained for your domain
   - Certificate is readable at `/etc/letsencrypt/live/your-domain/fullchain.pem`
   - Private key is readable at `/etc/letsencrypt/live/your-domain/privkey.pem`

2. ✅ **nginx Configured**
   - Updated `nginx.conf` includes SSL directives with certificate paths
   - Configuration syntax is valid: `nginx -t` returns success
   - nginx restarts without errors: `nginx -s reload` succeeds

3. ✅ **HTTPS Working**
   - Website is accessible via HTTPS on port 443
   - Browser does NOT show certificate warnings or errors
   - `curl -I https://your-domain` returns HTTP/1.1 200 or 3xx status
   - SSL certificate is properly validated: `openssl s_client -connect your-domain:443`

4. ✅ **HTTP Redirect**
   - Accessing HTTP (port 80) redirects to HTTPS
   - `curl -I http://your-domain` returns 301/302 redirect to HTTPS

5. ✅ **Auto-Renewal Configured**
   - Certbot renewal service is enabled
   - `certbot renew --dry-run` completes without errors
   - Renewal is scheduled to run automatically before certificate expiration

6. ✅ **Documentation Complete**
   - `/app/task_file/output/nginx.conf` contains the full SSL configuration
   - `/app/task_file/output/certificate-info.txt` documents certificate paths and details
   - `/app/task_file/output/verification-report.txt` shows all verification test results

## Commands Reference

```bash
# Install certbot and nginx plugin
sudo apt-get install certbot python3-certbot-nginx  # Debian/Ubuntu
# OR
sudo yum install certbot python3-certbot-nginx      # RHEL/CentOS

# Obtain certificate
sudo certbot certonly --nginx -d your-domain.com

# Verify nginx configuration
nginx -t

# Test certificate validation
openssl s_client -connect your-domain.com:443

# Check certificate expiration
certbot certificates

# Test renewal (dry-run)
sudo certbot renew --dry-run

# Restart nginx
sudo systemctl restart nginx
```

## Important Notes
- Replace `your-domain.com` with your actual domain name throughout
- Ensure DNS is properly configured before attempting certificate validation
- The Let's Encrypt certificate is valid for 90 days and must be renewed
- Certbot can automatically renew certificates before expiration
- Using Let's Encrypt provides trusted certificates at no cost
- This approach is suitable for production environments