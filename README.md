# D.R.A.C.S. — Dell Rack & Asset Control System

This Python-based CLI tool allows you to maintain a local inventory of Dell systems by combining live hardware data (via SNMP) with official warranty information (via the Dell Support API). It stores all data in a lightweight SQLite database for quick lookups and filtering.

🚀 Features
Warranty Tracking: Automatically fetches expiration dates from the Dell API using Service Tags.

Hardware Discovery: Uses SNMP to poll iDRAC and BIOS version information directly from the hardware.

Version Comparison: List and filter systems based on version strings (e.g., find all hosts with BIOS version less than 2.1.0).

Flexible Output: View inventory in a formatted grid table or export to JSON for automation.

SQLite Backend: No heavy database setup required; everything is stored in a local .db file.

🛠️ Prerequisites
Python 3.8+

Dell TechDirect API Credentials: You must have a Client ID and Secret from Dell to access warranty data.

SNMP Enabled: The target Dell systems must have SNMP enabled on their iDRACs (default community: public).

📦 Installation
1) Clone the repository:
```
git clone https://github.com/yourusername/dell-warranty-manager.git
cd dell-warranty-manager
```

2) Install dependencies:
```
pip install -r requirements.txt
```

3) Configure environment variables:
Create a .env file in the root directory:
```
CLIENT_ID=your_dell_client_id
CLIENT_SECRET=your_dell_client_secret
DEBUG=false
```

📖 Usage
The script uses subcommands for different operations: add, edit, lookup, list, and remove.

1. Add a New System
This polls the iDRAC for firmware/BIOS versions and the Dell API for warranty.
```
python3 dracs.py add --svctag ABC1234 --target server01.example.com --model R660
```

2. List Inventory
View all systems. You can filter by model, expiration, or version.
```
# List all systems
python3 dracs.py list

# List systems expiring in the next 30 days
python3 dracs.py list --expires_in 30

# List systems with a BIOS version older than 2.5.1
python3 dracs.py list --bios_lt 2.5.1
```

3. Lookup a Specific System
```
python3 dracs.py lookup --svctag ABC1234 --full
```

4. Update/Edit a System
Update the BIOS/iDRAC version in the database by re-polling the hardware.
```
python3 dracs.py edit --target server01.example.com --bios --idrac
```

5. Remove a System
```
python3 main.py remove --svctag ABC1234
```

⚙️ Arguments Summary
| Global Argument | Description                                                      |
| -d, --debug     | Enable verbose debug output.                                     |
| -w, --warranty  | Path to a custom SQLite database file (defaults to warranty.db). |

