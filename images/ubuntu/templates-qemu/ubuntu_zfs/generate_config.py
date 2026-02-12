#!/usr/bin/env python3
"""
Generate cloud-config from settings.json and cloud-config.yaml template.

Usage:
    python generate_config.py [--settings SETTINGS_FILE] [--template TEMPLATE_FILE] [--output OUTPUT_FILE]
"""

import argparse
import json
import re
import sys
from pathlib import Path


def load_settings(settings_path: Path) -> dict:
    """Load and validate settings from JSON file."""
    if not settings_path.exists():
        raise FileNotFoundError(f"Settings file not found: {settings_path}")
    
    with open(settings_path, 'r') as f:
        settings = json.load(f)
    
    # Validate required fields
    required_fields = [
        ('vm', 'name'),
        ('vm', 'disk_size_gb'),
        ('network', 'hostname'),
        ('user', 'username'),
        ('user', 'password_hash'),
        ('ssh', 'port'),
    ]
    
    for section, field in required_fields:
        if section not in settings:
            raise ValueError(f"Missing required section: {section}")
        if field not in settings[section]:
            raise ValueError(f"Missing required field: {section}.{field}")
    
    # Warn about placeholder values
    if 'REPLACE' in settings['user'].get('password_hash', ''):
        print("WARNING: password_hash contains placeholder value. Generate a real hash with:", file=sys.stderr)
        print("  mkpasswd -m sha-512", file=sys.stderr)
    
    if settings['user'].get('ssh_public_key', '').startswith('ssh-') and '...' in settings['user']['ssh_public_key']:
        print("WARNING: ssh_public_key appears to be a placeholder. Replace with your actual public key.", file=sys.stderr)
    
    return settings


def load_template(template_path: Path) -> str:
    """Load the cloud-config template."""
    if not template_path.exists():
        raise FileNotFoundError(f"Template file not found: {template_path}")
    
    with open(template_path, 'r') as f:
        return f.read()


def resolve_value(settings: dict, key_path: str) -> str:
    """Resolve a dotted key path to its value in settings."""
    keys = key_path.split('.')
    value = settings
    
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise KeyError(f"Cannot resolve key path: {key_path}")
    
    return value


def render_template(template: str, settings: dict) -> str:
    """Render the template with settings values using simple substitution."""
    result = template
    
    # Handle conditional expressions: {{ 'yes' if condition else 'no' }}
    conditional_pattern = r"\{\{\s*'([^']+)'\s+if\s+([a-zA-Z0-9_.]+)\s+else\s+'([^']+)'\s*\}\}"
    
    def replace_conditional(match):
        true_val = match.group(1)
        key_path = match.group(2)
        false_val = match.group(3)
        try:
            condition = resolve_value(settings, key_path)
            return true_val if condition else false_val
        except KeyError:
            return false_val
    
    result = re.sub(conditional_pattern, replace_conditional, result)
    
    # Handle for loops: {% for item in list %} ... {% endfor %}
    loop_pattern = r"\{%\s*for\s+(\w+)\s+in\s+([a-zA-Z0-9_.]+)\s*%\}(.*?)\{%\s*endfor\s*%\}"
    
    def replace_loop(match):
        item_name = match.group(1)
        list_path = match.group(2)
        loop_body = match.group(3)
        
        try:
            items = resolve_value(settings, list_path)
            if not isinstance(items, list):
                return ""
            
            output_lines = []
            for item in items:
                line = loop_body.replace(f"{{{{ {item_name} }}}}", str(item))
                output_lines.append(line)
            
            return ''.join(output_lines)
        except KeyError:
            return ""
    
    result = re.sub(loop_pattern, replace_loop, result, flags=re.DOTALL)
    
    # Handle if blocks: {% if condition %} ... {% endif %} and {% if condition %} ... {% else %} ... {% endif %}
    if_else_pattern = r"\{%\s*if\s+([a-zA-Z0-9_.]+)\s*%\}(.*?)\{%\s*else\s*%\}(.*?)\{%\s*endif\s*%\}"
    
    def replace_if_else(match):
        key_path = match.group(1)
        true_block = match.group(2)
        false_block = match.group(3)
        try:
            condition = resolve_value(settings, key_path)
            return true_block if condition else false_block
        except KeyError:
            return false_block
    
    result = re.sub(if_else_pattern, replace_if_else, result, flags=re.DOTALL)
    
    # Handle simple if blocks: {% if condition %} ... {% endif %}
    if_pattern = r"\{%\s*if\s+([a-zA-Z0-9_.]+)\s*%\}(.*?)\{%\s*endif\s*%\}"
    
    def replace_if(match):
        key_path = match.group(1)
        block = match.group(2)
        try:
            condition = resolve_value(settings, key_path)
            return block if condition else ""
        except KeyError:
            return ""
    
    result = re.sub(if_pattern, replace_if, result, flags=re.DOTALL)
    
    # Handle simple variable substitution: {{ variable }}
    var_pattern = r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}"
    
    def replace_var(match):
        key_path = match.group(1)
        try:
            value = resolve_value(settings, key_path)
            return str(value)
        except KeyError:
            print(f"WARNING: Unresolved template variable: {key_path}", file=sys.stderr)
            return match.group(0)
    
    result = re.sub(var_pattern, replace_var, result)
    
    return result


def main():
    parser = argparse.ArgumentParser(
        description='Generate cloud-config from settings and template'
    )
    parser.add_argument(
        '--settings', '-s',
        type=Path,
        default=Path(__file__).parent / 'settings.json',
        help='Path to settings.json file (default: settings.json in script directory)'
    )
    parser.add_argument(
        '--template', '-t',
        type=Path,
        default=Path(__file__).parent / 'cloud-config.yaml',
        help='Path to cloud-config.yaml template (default: cloud-config.yaml in script directory)'
    )
    parser.add_argument(
        '--output', '-o',
        type=Path,
        default=None,
        help='Output file path (default: stdout)'
    )
    parser.add_argument(
        '--validate-only', '-v',
        action='store_true',
        help='Only validate settings, do not generate output'
    )
    
    args = parser.parse_args()
    
    try:
        settings = load_settings(args.settings)
        
        if args.validate_only:
            print("Settings validation passed.", file=sys.stderr)
            return 0
        
        template = load_template(args.template)
        rendered = render_template(template, settings)
        
        if args.output:
            with open(args.output, 'w') as f:
                f.write(rendered)
            print(f"Generated config written to: {args.output}", file=sys.stderr)
        else:
            print(rendered)
        
        return 0
    
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
