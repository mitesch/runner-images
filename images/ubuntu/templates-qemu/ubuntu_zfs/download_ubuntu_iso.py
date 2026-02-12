#!/usr/bin/env python3
"""
Download the latest Ubuntu 24.04 LTS Live Server ISO with checksum verification.

Usage:
    python download_ubuntu_iso.py
    python download_ubuntu_iso.py -o /path/to/save/
"""

import argparse
import hashlib
import html.parser
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path


BASE_URL = "https://releases.ubuntu.com/24.04/"
ISO_PATTERN = re.compile(r'^ubuntu-24\.04(\.\d+)?-live-server-amd64\.iso$')


class LinkParser(html.parser.HTMLParser):
    """Parse HTML to extract links."""
    def __init__(self):
        super().__init__()
        self.links = []
    
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            for name, value in attrs:
                if name == 'href':
                    self.links.append(value)


def get_latest_iso_filename() -> str:
    """Fetch the Ubuntu releases page and find the latest server ISO filename."""
    print(f"Fetching release index from {BASE_URL}...", file=sys.stderr)
    
    try:
        with urllib.request.urlopen(BASE_URL, timeout=30) as response:
            html_content = response.read().decode('utf-8')
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to fetch release page: {e}")
    
    parser = LinkParser()
    parser.feed(html_content)
    
    # Find ISO files matching our pattern
    iso_files = [link for link in parser.links if ISO_PATTERN.match(link)]
    
    if not iso_files:
        raise RuntimeError("No Ubuntu 24.04 Live Server ISO found on release page")
    
    # Sort to get the latest point release (e.g., 24.04.1 > 24.04)
    iso_files.sort(reverse=True)
    return iso_files[0]


def get_checksum(iso_filename: str) -> str:
    """Download SHA256SUMS and extract checksum for the ISO."""
    sha256sums_url = BASE_URL + "SHA256SUMS"
    print(f"Fetching checksums from {sha256sums_url}...", file=sys.stderr)
    
    try:
        with urllib.request.urlopen(sha256sums_url, timeout=30) as response:
            content = response.read().decode('utf-8')
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to fetch checksums: {e}")
    
    # Parse SHA256SUMS format: "hash *filename" or "hash  filename"
    for line in content.splitlines():
        if iso_filename in line:
            parts = line.split()
            if len(parts) >= 2:
                return parts[0]
    
    raise RuntimeError(f"Checksum not found for {iso_filename}")


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def download_with_resume(url: str, output_path: Path, expected_size: int = None) -> None:
    """Download file with resume support and progress display."""
    
    # Check for existing partial download
    start_byte = 0
    if output_path.exists():
        start_byte = output_path.stat().st_size
        if expected_size and start_byte >= expected_size:
            print(f"File already fully downloaded: {output_path}", file=sys.stderr)
            return
        print(f"Resuming from byte {start_byte} ({format_size(start_byte)})...", file=sys.stderr)
    
    # Build request with Range header for resume
    request = urllib.request.Request(url)
    if start_byte > 0:
        request.add_header('Range', f'bytes={start_byte}-')
    
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            # Get total size
            if expected_size is None:
                content_length = response.headers.get('Content-Length')
                if content_length:
                    expected_size = int(content_length) + start_byte
            
            # Check if server supports resume
            if start_byte > 0 and response.status != 206:
                print("Server doesn't support resume, starting from beginning...", file=sys.stderr)
                start_byte = 0
                output_path.unlink()
            
            # Download with progress
            mode = 'ab' if start_byte > 0 else 'wb'
            downloaded = start_byte
            chunk_size = 1024 * 1024  # 1MB chunks
            
            with open(output_path, mode) as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    # Progress display
                    if expected_size:
                        percent = (downloaded / expected_size) * 100
                        print(f"\rDownloading: {format_size(downloaded)} / {format_size(expected_size)} ({percent:.1f}%)", 
                              end='', file=sys.stderr)
                    else:
                        print(f"\rDownloading: {format_size(downloaded)}", end='', file=sys.stderr)
            
            print(file=sys.stderr)  # Newline after progress
            
    except urllib.error.URLError as e:
        raise RuntimeError(f"Download failed: {e}")


def verify_checksum(file_path: Path, expected_hash: str) -> bool:
    """Verify SHA256 checksum of downloaded file."""
    print(f"Verifying checksum...", file=sys.stderr)
    
    sha256 = hashlib.sha256()
    file_size = file_path.stat().st_size
    processed = 0
    chunk_size = 1024 * 1024 * 10  # 10MB chunks for hashing
    
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            sha256.update(chunk)
            processed += len(chunk)
            percent = (processed / file_size) * 100
            print(f"\rVerifying: {percent:.1f}%", end='', file=sys.stderr)
    
    print(file=sys.stderr)  # Newline
    
    calculated = sha256.hexdigest()
    if calculated.lower() == expected_hash.lower():
        print("Checksum verified OK", file=sys.stderr)
        return True
    else:
        print(f"Checksum MISMATCH!", file=sys.stderr)
        print(f"  Expected: {expected_hash}", file=sys.stderr)
        print(f"  Got:      {calculated}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Download Ubuntu 24.04 LTS Live Server ISO'
    )
    parser.add_argument(
        '--output', '-o',
        type=Path,
        default=Path('.'),
        help='Output directory or file path (default: current directory)'
    )
    parser.add_argument(
        '--skip-verify',
        action='store_true',
        help='Skip checksum verification'
    )
    parser.add_argument(
        '--url-only',
        action='store_true',
        help='Only print the download URL, do not download'
    )
    
    args = parser.parse_args()
    
    try:
        # Find latest ISO
        iso_filename = get_latest_iso_filename()
        iso_url = BASE_URL + iso_filename
        
        if args.url_only:
            print(iso_url)
            return 0
        
        print(f"Found: {iso_filename}", file=sys.stderr)
        
        # Get checksum
        expected_hash = None
        if not args.skip_verify:
            expected_hash = get_checksum(iso_filename)
            print(f"Expected SHA256: {expected_hash}", file=sys.stderr)
        
        # Determine output path
        if args.output.is_dir():
            output_path = args.output / iso_filename
        else:
            output_path = args.output
        
        # Download
        print(f"Downloading to: {output_path}", file=sys.stderr)
        download_with_resume(iso_url, output_path)
        
        # Verify
        if expected_hash:
            if not verify_checksum(output_path, expected_hash):
                print("ERROR: Checksum verification failed!", file=sys.stderr)
                return 1
        
        print(f"Success: {output_path}", file=sys.stderr)
        return 0
        
    except (RuntimeError, KeyboardInterrupt) as e:
        if isinstance(e, KeyboardInterrupt):
            print("\nDownload interrupted", file=sys.stderr)
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
