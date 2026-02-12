#!/usr/bin/env python3
"""
Extract kernel (vmlinuz) and initrd from Ubuntu ISO.

Usage:
    python extract_kernel.py ubuntu-24.04-live-server-amd64.iso
    python extract_kernel.py ubuntu.iso -o /path/to/output/
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


# Files to extract from /casper/ directory
KERNEL_FILE = "vmlinuz"
INITRD_FILE = "initrd"


def find_extraction_tool() -> tuple[str, str]:
    """Find available tool for ISO extraction."""
    if shutil.which('isoinfo'):
        return 'isoinfo', 'isoinfo'
    if shutil.which('7z'):
        return '7z', '7z'
    if shutil.which('bsdtar'):
        return 'bsdtar', 'bsdtar'
    
    raise RuntimeError(
        "No ISO extraction tool found. Install one of:\n"
        "  Ubuntu/Debian: sudo apt install genisoimage\n"
        "  Or: sudo apt install p7zip-full\n"
        "  Or: sudo apt install libarchive-tools"
    )


def extract_with_isoinfo(iso_path: Path, file_path: str, output_path: Path) -> bool:
    """Extract a file from ISO using isoinfo."""
    # isoinfo uses uppercase paths and requires ;1 suffix for ISO9660
    # Try with and without ;1 suffix
    paths_to_try = [
        file_path + ".;1",
        file_path + ";1", 
        file_path,
        file_path.upper() + ".;1",
        file_path.upper() + ";1",
        file_path.upper(),
    ]
    
    for path in paths_to_try:
        cmd = [
            'isoinfo',
            '-i', str(iso_path),
            '-x', path,
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
            )
            
            if result.stdout and len(result.stdout) > 1000:
                output_path.write_bytes(result.stdout)
                return True
                
        except Exception:
            continue
    
    return False


def extract_with_7z(iso_path: Path, file_path: str, output_path: Path) -> bool:
    """Extract a file from ISO using 7z."""
    # 7z uses paths without leading /
    internal_path = file_path.lstrip('/')
    
    cmd = [
        '7z', 'e',
        '-so',  # Output to stdout
        str(iso_path),
        internal_path,
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=True,
        )
        
        if result.stdout:
            output_path.write_bytes(result.stdout)
            return True
        return False
        
    except subprocess.CalledProcessError:
        return False


def extract_with_bsdtar(iso_path: Path, file_path: str, output_path: Path) -> bool:
    """Extract a file from ISO using bsdtar."""
    import tempfile
    
    internal_path = file_path.lstrip('/')
    
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = [
            'bsdtar',
            '-xf', str(iso_path),
            '-C', tmpdir,
            internal_path,
        ]
        
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            extracted = Path(tmpdir) / internal_path
            if extracted.exists():
                shutil.copy(extracted, output_path)
                return True
            return False
            
        except subprocess.CalledProcessError:
            return False


def list_casper_contents(iso_path: Path) -> list[str]:
    """List files in /casper/ directory of ISO."""
    cmd = ['isoinfo', '-i', str(iso_path), '-l']
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        files = []
        in_casper = False
        for line in result.stdout.splitlines():
            line_lower = line.lower().strip()
            # Match both /casper/ and /CASPER/
            if 'directory listing of' in line_lower and '/casper' in line_lower:
                in_casper = True
                continue
            if in_casper:
                if 'directory listing of' in line_lower:
                    break
                # Parse isoinfo output format - filename is last field
                # Format: "---------- 0 0 0 size date [block] FILENAME.;1"
                # Skip directories (start with 'd')
                stripped = line.strip()
                if stripped and not stripped.startswith('d'):
                    parts = stripped.split()
                    if len(parts) >= 1:
                        # Get filename, remove version suffix (;1)
                        filename = parts[-1]
                        if ';' in filename:
                            filename = filename.split(';')[0]
                        # Remove trailing dot if present
                        filename = filename.rstrip('.')
                        if filename:
                            files.append(filename)
        
        return files
        
    except subprocess.CalledProcessError:
        return []


def extract_kernel(iso_path: Path, output_dir: Path) -> tuple[Path, Path]:
    """Extract kernel and initrd from ISO."""
    
    if not iso_path.exists():
        raise FileNotFoundError(f"ISO not found: {iso_path}")
    
    tool_name, _ = find_extraction_tool()
    print(f"Using extraction tool: {tool_name}", file=sys.stderr)
    
    # List casper contents to find exact filenames
    print(f"Scanning ISO contents...", file=sys.stderr)
    casper_files = list_casper_contents(iso_path)
    
    # Find kernel file (vmlinuz or VMLINUZ)
    kernel_name = None
    for f in casper_files:
        if f.upper() == 'VMLINUZ':
            kernel_name = f
            break
    
    if not kernel_name:
        raise RuntimeError(f"Kernel not found in ISO. Casper contents: {casper_files}")
    
    # Find initrd file
    initrd_name = None
    for f in casper_files:
        if f.upper() == 'INITRD':
            initrd_name = f
            break
    
    if not initrd_name:
        raise RuntimeError(f"Initrd not found in ISO. Casper contents: {casper_files}")
    
    print(f"Found kernel: {kernel_name}", file=sys.stderr)
    print(f"Found initrd: {initrd_name}", file=sys.stderr)
    
    # Output paths
    kernel_out = output_dir / KERNEL_FILE
    initrd_out = output_dir / INITRD_FILE
    
    # Extract based on tool
    extract_func = {
        'isoinfo': extract_with_isoinfo,
        '7z': extract_with_7z,
        'bsdtar': extract_with_bsdtar,
    }[tool_name]
    
    # Extract kernel
    print(f"Extracting {kernel_name}...", file=sys.stderr)
    if not extract_func(iso_path, f"/casper/{kernel_name}", kernel_out):
        raise RuntimeError(f"Failed to extract kernel")
    
    # Extract initrd
    print(f"Extracting {initrd_name}...", file=sys.stderr)
    if not extract_func(iso_path, f"/casper/{initrd_name}", initrd_out):
        raise RuntimeError(f"Failed to extract initrd")
    
    # Verify sizes
    kernel_size = kernel_out.stat().st_size
    initrd_size = initrd_out.stat().st_size
    
    print(f"Extracted: {kernel_out} ({kernel_size:,} bytes)", file=sys.stderr)
    print(f"Extracted: {initrd_out} ({initrd_size:,} bytes)", file=sys.stderr)
    
    if kernel_size < 1000000:  # Kernel should be > 1MB
        raise RuntimeError(f"Kernel file seems too small ({kernel_size} bytes)")
    
    if initrd_size < 10000000:  # Initrd should be > 10MB
        raise RuntimeError(f"Initrd file seems too small ({initrd_size} bytes)")
    
    return kernel_out, initrd_out


def main():
    parser = argparse.ArgumentParser(
        description='Extract kernel and initrd from Ubuntu ISO'
    )
    parser.add_argument(
        'iso',
        type=Path,
        help='Path to Ubuntu ISO file'
    )
    parser.add_argument(
        '--output', '-o',
        type=Path,
        default=None,
        help='Output directory (default: same directory as ISO)'
    )
    parser.add_argument(
        '--check-only', '-c',
        action='store_true',
        help='Only check if extraction is needed'
    )
    
    args = parser.parse_args()
    
    # Determine output directory
    if args.output:
        output_dir = args.output
    else:
        output_dir = args.iso.parent
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Check if already extracted
    kernel_path = output_dir / KERNEL_FILE
    initrd_path = output_dir / INITRD_FILE
    
    if kernel_path.exists() and initrd_path.exists():
        print(f"Kernel and initrd already exist in {output_dir}", file=sys.stderr)
        if args.check_only:
            return 0
        print("Re-extracting...", file=sys.stderr)
    
    if args.check_only:
        print("Extraction needed", file=sys.stderr)
        return 1
    
    try:
        kernel_out, initrd_out = extract_kernel(args.iso, output_dir)
        print(f"\nSuccess! Files extracted to {output_dir}", file=sys.stderr)
        return 0
        
    except (FileNotFoundError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
