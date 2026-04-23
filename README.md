# D.R.A.C.S. — Dell Rack & Asset Control System

Simple, portable, self-contained dynamic CLI inventory tool for managing Dell bare-metal systems inventory, warranty and lifecycle.

- Plugs directly into Dell Support API
- Live hardware data management via SNMP
- Utilizes a portable SQLite database
- Supports regex and simple search patterns
- Easily extensible to scripting and automation
  <!--toc:start-->
  - [🚀 Features](#-features)
  - [🛠️ Prerequisites](#%EF%B8%8F-prerequisites)
  - [📦 Installation](#-installation)
  - [📖 Usage](#-usage)
    - [1. Add a New System](#1-add-a-new-system)
    - [2. Discover a System](#2-discover-a-system)
    - [3. List Inventory](#3-list-inventory)
    - [4. Lookup a Specific System](#4-lookup-a-specific-system)
    - [5. Edit a System](#5-edit-a-system)
    - [6. Refresh System Data](#6-refresh-system-data)
    - [7. Remove a System](#7-remove-a-system)
    - [Common Usage Patterns](#common-usage-patterns)
  - [⚙️ Command Reference](#-command-reference)
    - [Global Arguments (apply to all commands)](#global-arguments-apply-to-all-commands)
    - [Command Aliases](#command-aliases)
    - [Filter Options for `list` Command](#filter-options-for-list-command)
  - [📝 Tips & Troubleshooting](#-tips-troubleshooting)
  <!--toc:end-->

## 🚀 Features

- **Warranty Tracking:** Automatically fetches expiration dates from the Dell API using Service Tags
- **Hardware Discovery:** Uses SNMP to poll iDRAC and BIOS version information directly from the hardware
- **Data Refresh:** Update both SNMP hardware data AND warranty information for existing systems
- **Version Comparison:** List and filter systems based on version strings (e.g., find all hosts with BIOS version less than 2.1.0)
- **Flexible Output:** View inventory in a formatted grid table or export to JSON for automation
- **Verbose Logging:** Optional verbose (`-v`) and debug (`-d`) modes for detailed progress tracking
- **SQLite Backend:** No heavy database setup required; everything is stored in a local .db file
- **Command Aliases:** Short aliases for all commands (e.g., `a` for add, `li` for list, `rf` for refresh)

## 🛠️ Prerequisites

- **Python 3.8+** (tested with Python 3.14)
- **Dell TechDirect API Credentials:** You must have a Client ID and Secret from Dell to access warranty data
- **SNMP Enabled:** The target Dell systems must have SNMP enabled on their iDRACs (default community: public)
- **Network Access:** Ability to reach Dell iDRAC interfaces via DNS (naming convention configured via DRACS_DNS_STRING and DRACS_DNS_MODE)

## 📦 Installation

**1) Clone the repository:**

```
git clone https://github.com/yourusername/dell-warranty-manager.git
cd dell-warranty-manager
```

**2) Create a virtual environment (recommended):**

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

**3) Install dependencies:**

```bash
pip install -r requirements.txt
```

**4) Configure environment variables:**
Create a .env file in the root directory:

```bash
# Required: Dell TechDirect API credentials
CLIENT_ID=your_dell_client_id
CLIENT_SECRET=your_dell_client_secret

# Required: iDRAC DNS configuration
# DRACS_DNS_STRING: String to add to hostname for iDRAC FQDN
# DRACS_DNS_MODE: How to add the string ('prefix' or 'suffix')
#
# Examples:
# Prefix mode: "mgmt-" + "host01.example.com" = "mgmt-host01.example.com"
DRACS_DNS_STRING=mgmt-
DRACS_DNS_MODE=prefix
#
# Suffix mode: "host01" + "-mm" + ".example.com" = "host01-mm.example.com"
# DRACS_DNS_STRING=-mm
# DRACS_DNS_MODE=suffix

# Optional: SNMP community string (defaults to 'public')
SNMP_COMMUNITY=public

# Optional: Enable debug logging via environment (can also use -d flag)
DEBUG=false
```

**Note:** Obtain Dell API credentials from [Dell TechDirect](https://techdirect.dell.com)

## 📖 Usage

The script uses subcommands for different operations: **add**, **discover**, **edit**, **lookup**, **list**, **refresh**, and **remove**.

### 1. Add a New System

This polls the iDRAC for firmware/BIOS versions and the Dell API for warranty.

```bash
# Add a system (full command)
python3 dracs.py add --svctag ABC1234 --target server01.example.com --model R660

# Using alias and verbose output
python3 dracs.py -v a -s ABC1234 -t server01.example.com -m R660

# With custom database path
python3 dracs.py -w /path/to/custom.db add -s ABC1234 -t server01 -m R650
```

### 2. Discover a System

Automatically discover service tag and model information via SNMP, then optionally add to database.

```bash
# Discover a system (prompts for confirmation)
python3 dracs.py discover --target server01.example.com

# Using alias
python3 dracs.py d -t server01.example.com

# Auto-add without prompting
python3 dracs.py discover --target server01.example.com --add

# With verbose output
python3 dracs.py -v discover -t server01.example.com

# Discover and auto-add using alias
python3 dracs.py d -t server01.example.com --add
```

### 3. List Inventory

View all systems. You can filter by model, expiration, or version. Results are always sorted by hostname.

```bash
# List all systems
python3 dracs.py list

# List all systems (using alias)
python3 dracs.py li

# List systems by model
python3 dracs.py list --model R660

# List systems expiring in the next 30 days
python3 dracs.py list --expires_in 30

# List systems with hostname matching a pattern
python3 dracs.py list --regex "server%"

# Filter by BIOS version
python3 dracs.py list --bios_lt 2.5.1        # BIOS less than 2.5.1
python3 dracs.py list --bios_le 2.5.1        # BIOS less than or equal to 2.5.1
python3 dracs.py list --bios_gt 2.5.1        # BIOS greater than 2.5.1
python3 dracs.py list --bios_ge 2.5.1        # BIOS greater than or equal to 2.5.1
python3 dracs.py list --bios_eq 2.5.1        # BIOS equal to 2.5.1

# Filter by iDRAC firmware version
python3 dracs.py list --idrac_lt 6.10.30.00  # iDRAC less than 6.10.30.00
python3 dracs.py list --idrac_ge 6.10.30.00  # iDRAC greater than or equal

# Output as JSON for automation
python3 dracs.py list --json

# Output only hostnames (one per line) - useful for scripting
python3 dracs.py list --host-only
python3 dracs.py list --model R650 --host-only

# Complex filter: R660 systems with old BIOS expiring soon
python3 dracs.py list --model R660 --bios_lt 2.5.0 --expires_in 60

# Lookup specific system in list format
python3 dracs.py list --svctag ABC1234
python3 dracs.py list --target server01.example.com
```

### 4. Lookup a Specific System

Retrieve detailed information about a single system.

```bash
# Lookup by service tag with all fields
python3 dracs.py lookup --svctag ABC1234 --full

# Lookup by hostname
python3 dracs.py lookup --target server01.example.com --full

# Show only BIOS version
python3 dracs.py lookup --svctag ABC1234 --bios

# Show only iDRAC firmware version
python3 dracs.py lookup -s ABC1234 --idrac

# Using alias
python3 dracs.py l -t server01.example.com --full
```

### 5. Edit a System

Update specific fields in the database by re-polling hardware or changing model.

```bash
# Update both BIOS and iDRAC versions from SNMP
python3 dracs.py edit --target server01.example.com --bios --idrac

# Update by service tag
python3 dracs.py edit --svctag ABC1234 --bios --idrac

# Update only BIOS version
python3 dracs.py edit -t server01 --bios

# Update only iDRAC firmware version
python3 dracs.py edit -s ABC1234 --idrac

# Change model name
python3 dracs.py edit -s ABC1234 --model R650

# Using alias with verbose output
python3 dracs.py -v e -t server01 --bios --idrac
```

### 6. Refresh System Data

Refresh both SNMP data (BIOS/iDRAC versions) AND warranty information from Dell API.
This is useful when service contracts are renewed or firmware is updated.

```bash
# Refresh by service tag
python3 dracs.py refresh --svctag ABC1234

# Refresh by hostname
python3 dracs.py refresh --target server01.example.com

# Using alias with verbose output to see progress
python3 dracs.py -v rf -s ABC1234

# With debug output
python3 dracs.py -d refresh -t server01
```

### 7. Remove a System

Delete a system from the database.

```bash
# Remove by service tag
python3 dracs.py remove --svctag ABC1234

# Remove by hostname
python3 dracs.py remove --target server01.example.com

# Using alias
python3 dracs.py r -s ABC1234
```

### Common Usage Patterns

```bash
# Initial setup: Add all your systems
python3 dracs.py -v add -s ABC1234 -t server01.example.com -m R660
python3 dracs.py -v add -s DEF5678 -t server02.example.com -m R650
python3 dracs.py -v add -s GHI9012 -t server03.example.com -m R660

# Or discover and add systems automatically
python3 dracs.py -v discover -t server04.example.com --add
python3 dracs.py -v d -t server05.example.com --add

# Discover a system but confirm before adding
python3 dracs.py discover -t server06.example.com

# Check what's expiring soon
python3 dracs.py list --expires_in 30

# Find systems that need firmware updates
python3 dracs.py list --idrac_lt 6.10.30.00

# After updating firmware, refresh the data
python3 dracs.py -v refresh -t server01.example.com

# After renewing support contracts, refresh warranty
python3 dracs.py -v refresh -s ABC1234

# Generate JSON report for external tools
python3 dracs.py list --json > inventory.json

# Get list of all hostnames for scripting (e.g., to feed to xargs or a loop)
python3 dracs.py list --host-only > hostnames.txt
for host in $(python3 dracs.py list --model R650 --host-only); do echo "Processing $host"; done

# Find all R660 models
python3 dracs.py list --model R660

# Detailed troubleshooting with debug output
python3 dracs.py -d add -s ABC1234 -t server01 -m R660
```

## ⚙️ Command Reference

### Global Arguments (apply to all commands)

| Argument         | Description                                                     |
| ---------------- | --------------------------------------------------------------- |
| `-h, --help`     | Show help message and exit                                      |
| `-d, --debug`    | Enable debug mode (most detailed output, includes SQL queries)  |
| `-v, --verbose`  | Enable verbose output (shows INFO level progress messages)      |
| `-w, --warranty` | Path to a custom SQLite database file (defaults to warranty.db) |

### Command Aliases

| Full Command | Alias | Required Arguments                       | Optional Arguments                                                                                     |
| ------------ | ----- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `add`        | `a`   | `-s/--svctag` `-t/--target` `-m/--model` | None                                                                                                   |
| `discover`   | `d`   | `-t/--target`                            | `--add`                                                                                                |
| `edit`       | `e`   | `-s/--svctag` OR `-t/--target`           | `--bios` `--idrac` `--model`                                                                           |
| `lookup`     | `l`   | `-s/--svctag` OR `-t/--target`           | `--full` `--bios` `--idrac`                                                                            |
| `list`       | `li`  | None                                     | `--model` `--regex` `--expires_in` `--svctag` `--target` `--bios_*` `--idrac_*` `--json` `--host-only` |
| `refresh`    | `rf`  | `-s/--svctag` OR `-t/--target`           | None                                                                                                   |
| `remove`     | `r`   | `-s/--svctag` OR `-t/--target`           | None                                                                                                   |

### Filter Options for `list` Command

**BIOS Version Filters:**

- `--bios_lt VERSION` - BIOS less than VERSION
- `--bios_le VERSION` - BIOS less than or equal to VERSION
- `--bios_gt VERSION` - BIOS greater than VERSION
- `--bios_ge VERSION` - BIOS greater than or equal to VERSION
- `--bios_eq VERSION` - BIOS equal to VERSION

**iDRAC Firmware Filters:**

- `--idrac_lt VERSION` - iDRAC less than VERSION
- `--idrac_le VERSION` - iDRAC less than or equal to VERSION
- `--idrac_gt VERSION` - iDRAC greater than or equal to VERSION
- `--idrac_ge VERSION` - iDRAC greater than VERSION
- `--idrac_eq VERSION` - iDRAC equal to VERSION

**Other Filters:**

- `--model MODEL` - Filter by server model (e.g., R650, R660)
- `--regex PATTERN` - Filter hostname by SQL LIKE pattern
- `--expires_in DAYS` - Systems with warranty expiring in N days

**Output Options:**

- `--json` - Output results as JSON instead of table
- `--host-only` - Output only hostnames, one per line (useful for scripting)

## 📝 Tips & Troubleshooting

**Using Verbose Output:**
Always use `-v` when running commands interactively to see progress:

```bash
python3 dracs.py -v add -s ABC1234 -t server01 -m R660
```

**SNMP Connectivity:**

- Ensure the iDRAC interface is reachable using the DNS naming configured in `DRACS_DNS_STRING` and `DRACS_DNS_MODE`
- Default SNMP community is `public` (configure via `SNMP_COMMUNITY` env var)
- Port 161 must be accessible

**Dell API Issues:**

- Verify credentials in `.env` file
- Check service tag is valid (5-7 alphanumeric characters)
- Ensure system has an active Dell warranty/support contract

**Database Location:**

- Default: `warranty.db` in the same directory as `dracs.py`
- Custom: Use `-w /path/to/custom.db` flag
- The database is created automatically on first use

**Debug Mode:**
Use `-d` flag to see detailed debugging including:

- SQL queries and parameters
- SNMP OID requests and responses
- Dell API request/response details
- Internal data structures

```bash
python3 dracs.py -d add -s ABC1234 -t server01 -m R660
```
