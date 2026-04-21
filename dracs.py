#!/usr/bin/env python3

import argparse
import asyncio
import json
import os
import re
import requests
import sqlite3
import sys
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path
from pysnmp.hlapi.v3arch.asyncio import *
from tabulate import tabulate

def db_initialize(dbpath):
    """
    Initializes the SQLite database. Creates the 'systems' table if it does not
    already exist in the specified file path.
    """
    conn = sqlite3.connect(dbpath)
    cursor = conn.cursor()

    # Create table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS systems (
            svc_tag TEXT PRIMARY KEY,
            name TEXT,
            model TEXT,
            idrac_version TEXT,
            bios_version TEXT,
            exp_date TEXT,
            exp_epoch INTEGER
        )
    ''')
    conn.commit()
    conn.close()
    return

async def get_snmp_value(target, community, oid):
    """
    Asynchronously queries a specific SNMP OID from a target host.
    Used here to pull BIOS and iDRAC firmware versions from Dell servers.
    """
    snmp_engine = SnmpEngine()

    # Standard SNMP v2c Get Command
    errorIndication, errorStatus, errorIndex, varBinds = await get_cmd(
        snmp_engine,
        CommunityData(community),
        await UdpTransportTarget.create((target, 161)),
        ContextData(),
        ObjectType(ObjectIdentity(oid))
    )

    if errorIndication:
        print(f"Error: {errorIndication}")
        return None
    elif errorStatus:
        print(f"Error: {errorStatus.prettyPrint()} at {errorIndex}")
        return None
    else:
        for varBind in varBinds:
            return varBind[1].prettyPrint()

def dell_api_warranty_date(svctag):
    """
    Authenticates with Dell's OAuth2 API and fetches the latest warranty
    expiration date for a given service tag. Returns a tuple of (epoch, string).
    """
    if svctag == None:
        print("Error! Need to provide parameter svctag")
        exit(1)

    # Your credentials from TechDirect
    CLIENT_ID = os.getenv("CLIENT_ID")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")
    TOKEN_URL = "https://apigtwb2c.us.dell.com/auth/oauth/v2/token" # Verify current URL in TechDirect docs

    # Fetch the token
    auth_response = requests.post(
        TOKEN_URL,
        data={"grant_type": "client_credentials"},
        auth=(CLIENT_ID, CLIENT_SECRET)
    )

    token = auth_response.json().get("access_token")

    WARRANTY_API_URL = "https://apigtwb2c.us.dell.com/PROD/sbil/eapi/v5/asset-entitlements"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    payload = {
        "servicetags": [svctag]
    }

    response = requests.get(WARRANTY_API_URL, headers=headers, params=payload)

    if response.status_code == 200:
        warranty_data = response.json()
    else:
        print(f"Error: {response.status_code} - {response.text}")
        exit(1)

    for s in warranty_data:
        svctag = s["serviceTag"]
        entitlements = s["entitlements"]

    cur_eed = 0
    cur_eed_string = "January 1, 1970"
    for e in entitlements:
        eed = e["endDate"]
        eed_dt = datetime.fromisoformat(eed.replace("Z", "+00:00"))
        eed_dt_epoch = int(eed_dt.strftime("%s"))
        eed_dt_string = eed_dt.strftime("%B %e, %Y")
        if eed_dt_epoch > cur_eed:
            cur_eed = eed_dt_epoch
            cur_eed_string = eed_dt_string

    return (cur_eed, cur_eed_string)

async def add_dell_warranty(service_tag, hostname, model, warranty):
    """
    Logic for the 'add' command. Fetches hardware versions via SNMP and
    warranty dates via API, then saves the new record to the local DB.
    """
    idrac_host = "mgmt-" + hostname
    community_string = 'public'
    BIOS_OID = '1.3.6.1.4.1.674.10892.5.4.300.50.1.8.1.1'
    IDRAC_FW_OID = '1.3.6.1.4.1.674.10892.5.1.1.8.0'

    bios_version = await get_snmp_value(idrac_host, community_string, BIOS_OID)
    idrac_version = await get_snmp_value(idrac_host, community_string, IDRAC_FW_OID)
    db_initialize(warranty)
    conn = sqlite3.connect(warranty)
    cursor = conn.cursor()
    query = """
        SELECT * FROM systems
        WHERE svc_tag = :service_tag
           AND name = :hostname
    """
    params = {'service_tag': service_tag, 'hostname': hostname }
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()
    if debug_output:
        print(f"service_tag = {service_tag}")
        print(f"hostname = {hostname}")
        print(f"warranty = {warranty}")
        print(f"query = {query}")
        print(f"params = {params}")
        print(results)

    if len(results) > 1:
        print("DB Error!")
        exit(1)

    # If the host is already in the DB, then we
    # update the FW and BIOS versions, as well as model.
    # No need to reach out to Dell to refetch warranty
    if len(results) == 1:
        exp_date = results[0][5]
        exp_epoch = results[0][6]
        conn = sqlite3.connect(warranty)
        cursor = conn.cursor()
        data = {
            "svc_tag": service_tag,
            "name": hostname,
            "model": model,
            "idrac_version": idrac_version,
            "bios_version": bios_version,
            "exp_date": exp_date,
            "exp_epoch": exp_epoch
        }
        # Insert data
        cursor.execute('''
            INSERT OR REPLACE INTO systems VALUES (:svc_tag, :name, :model, :idrac_version, :bios_version, :exp_date, :exp_epoch)
        ''', data)
        conn.commit()
        conn.close()
    else:
        # get warranty from Dell API
        (h_epoch, h_date) = dell_api_warranty_date(service_tag)
        result = {"svctag": service_tag}
        result["exp_date"] = h_date
        result["exp_epoch"] = h_epoch
        result["hostname"] = hostname
        result["model"] = model
        result["bios_version"] = bios_version
        result["idrac_version"] = idrac_version

        if debug_output:
            print(result)
        conn = sqlite3.connect(warranty)
        cursor = conn.cursor()
        data = {
            "svc_tag": service_tag,
            "name": hostname,
            "model": model,
            "idrac_version": idrac_version,
            "bios_version": bios_version,
            "exp_date": result["exp_date"],
            "exp_epoch": result["exp_epoch"]
        }
        # Insert data
        cursor.execute('''
            INSERT OR REPLACE INTO systems VALUES (:svc_tag, :name, :model, :idrac_version, :bios_version, :exp_date, :exp_epoch)
        ''', data)
        conn.commit()
        conn.close()
        if debug_output:
            print("DB Updated")

async def edit_dell_warranty(service_tag, hostname, model, idrac, bios, warranty):
    """
    Logic for the 'edit' command. Allows updating specific fields (model, BIOS, iDRAC)
    for an existing record in the database without re-fetching warranty dates.
    """
    if service_tag:
        if debug_output:
            print(f"service_tag = {service_tag}")
    if hostname:
        if debug_output:
            print(f"hostname = {hostname}")
    if model:
        if debug_output:
            print(f"model = {model}")
    else:
        if not idrac and not bios:
            print("Error: no model supplied for edit mode. Please use --model argument")
            exit(1)

    db_initialize(warranty)
    conn = sqlite3.connect(warranty)
    cursor = conn.cursor()
    if service_tag:
        query = """
            SELECT * FROM systems
            WHERE svc_tag = :service_tag
        """
        params = {'service_tag': service_tag }
    if hostname:
        query = """
            SELECT * FROM systems
            WHERE name = :hostname
        """
        params = {'hostname': hostname }
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()

    if debug_output:
        print(f"service_tag = {service_tag}")
        print(f"hostname = {hostname}")
        print(f"warranty = {warranty}")
        print(f"query = {query}")
        print(f"params = {params}")
        print(results)

    if len(results) > 1:
        print("DB Error!")
        exit(1)

    if len(results) == 1:
        hostname = results[0][1]
        idrac_host = "mgmt-" + hostname
        community_string = 'public'
        BIOS_OID = '1.3.6.1.4.1.674.10892.5.4.300.50.1.8.1.1'
        IDRAC_FW_OID = '1.3.6.1.4.1.674.10892.5.1.1.8.0'

        if idrac:
            idrac_version = await get_snmp_value(idrac_host, community_string, IDRAC_FW_OID)
        else:
            idrac_version = results[0][3]
        if bios:
            bios_version = await get_snmp_value(idrac_host, community_string, BIOS_OID)
        else:
            bios_version = results[0][4]
        if not model:
            model = results[0][2]
        exp_date = results[0][5]
        exp_epoch = results[0][6]
        conn = sqlite3.connect(warranty)
        cursor = conn.cursor()
        data = {
            "svc_tag": results[0][0],
            "name": results[0][1],
            "model": model,
            "idrac_version": idrac_version,
            "bios_version": bios_version,
            "exp_date": exp_date,
            "exp_epoch": exp_epoch
        }
        # Insert data
        cursor.execute('''
            INSERT OR REPLACE INTO systems VALUES (:svc_tag, :name, :model, :idrac_version, :bios_version, :exp_date, :exp_epoch)
        ''', data)
        conn.commit()
        conn.close()
        if debug_output:
            print("DB Updated!")
    else:
        print("Record not found!")
        exit(1)
    return

async def lookup_dell_warranty(service_tag, hostname, idrac, bios, full, warranty):
    """
    Logic for the 'lookup' command. Retrieves a single system's data from
    the DB and prints it to the console in dictionary format.
    """
    db_initialize(warranty)
    conn = sqlite3.connect(warranty)
    cursor = conn.cursor()
    if service_tag:
        query = """
            SELECT * FROM systems
            WHERE svc_tag = :service_tag
        """
        params = {'service_tag': service_tag }
    if hostname:
        query = """
            SELECT * FROM systems
            WHERE name = :hostname
        """
        params = {'hostname': hostname }
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()
    if len(results) == 0:
        print("No records found!")
        exit(1)
    if len(results) > 1:
        print("DB Error!")
        exit(1)
    if len(results) == 1:
        hostname = results[0][1]
        result = {"hostname": hostname}
        model = results[0][2]
        if idrac or full:
            idrac_version = results[0][3]
            result["idrac_version"] = idrac_version
        if bios or full:
            bios_version = results[0][4]
            result["bios_version"] = bios_version
        result["svc_tag"] = results[0][0]
        if not idrac and not bios:
            result["model"] = model
            result["exp_date"] = results[0][5]
            result["exp_epoch"] = results[0][6]
        print(result)
    else:
        print("Record not found!")
        exit(1)
    return

async def filter_list_results(results, bios_le, bios_lt, bios_ge, bios_gt, bios_eq,
                              idrac_le, idrac_lt, idrac_ge, idrac_gt, idrac_eq):
    """
    Helper function to filter a list of systems based on version comparison.
    Converts version strings (e.g., '2.1.1') into tuples for proper numeric comparison.
    """
    output = []
    # columns are svc_tag,hostname,model,idrac_version,bios_version,exp_string,exp_epoch
    for s in results:
        s_idrac = s[3]
        s_idrac_tuple = tuple(map(int, s_idrac.split('.')))
        s_bios = s[4]
        s_bios_tuple = tuple(map(int, s_bios.split('.')))
        if idrac_le:
            idrac_le_tuple = tuple(map(int, idrac_le.split('.')))
            if s_idrac_tuple <= idrac_le_tuple:
                output.append(s)
        if idrac_lt:
            idrac_lt_tuple = tuple(map(int, idrac_lt.split('.')))
            if s_idrac_tuple < idrac_lt_tuple:
                output.append(s)
        if idrac_ge:
            idrac_ge_tuple = tuple(map(int, idrac_ge.split('.')))
            if s_idrac_tuple >= idrac_ge_tuple:
                output.append(s)
        if idrac_gt:
            idrac_gt_tuple = tuple(map(int, idrac_gt.split('.')))
            if s_idrac_tuple > idrac_gt_tuple:
                output.append(s)
        if idrac_eq:
            idrac_eq_tuple = tuple(map(int, idrac_eq.split('.')))
            if s_idrac_tuple == idrac_eq_tuple:
                output.append(s)
        if bios_le:
            bios_le_tuple = tuple(map(int, bios_le.split('.')))
            if s_bios_tuple <= bios_le_tuple:
                output.append(s)
        if bios_lt:
            bios_lt_tuple = tuple(map(int, bios_lt.split('.')))
            if s_bios_tuple < bios_lt_tuple:
                output.append(s)
        if bios_ge:
            bios_ge_tuple = tuple(map(int, bios_ge.split('.')))
            if s_bios_tuple >= bios_ge_tuple:
                output.append(s)
        if bios_gt:
            bios_gt_tuple = tuple(map(int, bios_gt.split('.')))
            if s_bios_tuple > bios_gt_tuple:
                output.append(s)
        if bios_eq:
            bios_eq_tuple = tuple(map(int, bios_eq.split('.')))
            if s_bios_tuple == bios_eq_tuple:
                output.append(s)

    return output

async def list_dell_warranty(service_tag, hostname, model, regex,
                             bios_le, bios_lt, bios_ge, bios_gt, bios_eq,
                             idrac_le, idrac_lt, idrac_ge, idrac_gt, idrac_eq,
                             expires_in, printjson, warranty):
    """
    Logic for the 'list' command. Performs complex SQL queries based on filters
    (model, regex, expiration time) and outputs results in JSON or Grid table format.
    """
    db_initialize(warranty)
    conn = sqlite3.connect(warranty)
    cursor = conn.cursor()
    # default query
    query = """
            SELECT * FROM systems
            WHERE svc_tag LIKE '%'
    """
    params = {}
    if service_tag and hostname:
        print("Cannot specify both --svctag and --target; they are mutually exclusive")
        exit(1)
    if service_tag:
        query = """
            SELECT * FROM systems
            WHERE svc_tag = :service_tag
        """
        params = {'service_tag': service_tag }
    if hostname:
        query = """
            SELECT * FROM systems
            WHERE name = :hostname
        """
        params = {'hostname': hostname }

    if hostname or service_tag:
        if model or regex:
            print("Cannot specify --model or --regex when using --svctag or --target")
            exit(1)

    if model and regex:
        query = """
            SELECT * from systems
            WHERE name LIKE :regex AND model = :model
        """
        params = {'regex': regex, 'model': model}

    if model and not regex:
        query = """
            SELECT * from systems
            WHERE model = :model
        """
        params = {'model': model}

    if not model and regex:
        query = """
            SELECT * from systems
            WHERE name LIKE :regex
        """
        params = {'regex': regex}

    if expires_in:
        timestamp = int(time.time()) + (int(expires_in) * 86400)
        query += "AND exp_epoch <= :timestamp\n"
        params['timestamp'] = timestamp
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()
    if bios_le or bios_lt or bios_ge or bios_gt or bios_eq or \
            idrac_le or idrac_lt or idrac_ge or idrac_gt or idrac_eq:
        results = await filter_list_results(results, bios_le, bios_lt, bios_ge, bios_gt, bios_eq,
                                      idrac_le, idrac_lt, idrac_ge, idrac_gt, idrac_eq)
    if printjson:
        print(json.dumps(results, indent=4))
    else:
        headers = ["Service Tag", "Hostname", "Model", "Firmware", "BIOS", "Expires", "Timestamp"]
        print(tabulate(results, headers=headers, tablefmt="grid"))
    return

async def remove_dell_warranty(service_tag, hostname, warranty):
    """
    Logic for the 'remove' command. Deletes a system record from the
    database by service tag or hostname.
    """
    if service_tag:
        if debug_output:
            print(f"service_tag = {service_tag}")
    if hostname:
        if debug_output:
            print(f"hostname = {hostname}")

    db_initialize(warranty)
    conn = sqlite3.connect(warranty)
    cursor = conn.cursor()
    if service_tag:
        query = """
            SELECT * FROM systems
            WHERE svc_tag = :service_tag
        """
        params = {'service_tag': service_tag }
    if hostname:
        query = """
            SELECT * FROM systems
            WHERE name = :hostname
        """
        params = {'hostname': hostname }
    cursor.execute(query, params)
    results = cursor.fetchall()
    conn.close()
    if len(results) == 0:
        print("No records found!")
        exit(1)
    if len(results) > 1:
        print("DB Error!")
        exit(1)
    if len(results) == 1:
        hostname = results[0][1]
        model = results[0][2]
        result = {"hostname": hostname}
        result["svc_tag"] = results[0][0]
        service_tag = result["svc_tag"]
        query = """
            DELETE FROM systems
            WHERE svc_tag = :service_tag
        """
        params = {'service_tag': result["svc_tag"] }
        conn = sqlite3.connect(warranty)
        cursor = conn.cursor()
        cursor.execute(query, params)
        if cursor.rowcount == 0:
            print(f"No system found with svctag {service_tag}.")
        else:
            conn.commit()
            print("Record deleted")
        conn.close()
    return

class CustomParser(argparse.ArgumentParser):
    """
    Extended ArgumentParser to provide customized error messages 
    when no sub-command (add, edit, etc.) is provided.
    """
    def error(self, message):
        # Check if the error is specifically about the missing subparser
        if 'required: command' in message:
            print("\nError: One of the following modes must be used:\n")
            print("    add (a)         Add a system")
            print("    edit (e)        Edit a system")
            print("    lookup (l)      Lookup a system")
            print("    remove (r)      Remove a system")
            print("    list (li)       List systems\n")
            self.print_usage()
            sys.exit(2)
        # Fall back to default behavior for other errors
        super().error(message)

async def main():
    """
    Main entry point. Configures CLI arguments, subparsers for commands,
    handles global debug settings, and routes execution to the appropriate logic.
    """
    parser = CustomParser(description="System Warranty Database Manager")

    # Global Optional Argument
    parser.add_argument('-d', '--debug', action='store_true', help="Enable debug mode")
    parser.add_argument('-w', '--warranty', help="Path to SQLite warranty.db")

    # Create Subparsers (This makes -a, -e, -l, -r mutually exclusive)
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- ADD COMMAND ---
    parser_add = subparsers.add_parser('add', aliases=['a'], help="Add a system")
    parser_add.add_argument('-s', '--svctag', required=True, help="Service tag")
    parser_add.add_argument('-t', '--target', required=True, help="DNS Hostname")
    parser_add.add_argument('-m', '--model', required=True, help="System model (e.g. R660)")

    # --- EDIT COMMAND ---
    parser_edit = subparsers.add_parser('edit', aliases=['e'], help="Edit a system")
    # Mutually exclusive group: Must have tag OR target
    edit_group = parser_edit.add_mutually_exclusive_group(required=True)
    edit_group.add_argument('-s', '--svctag', help="Service tag to edit")
    edit_group.add_argument('-t', '--target', help="Target hostname to edit")
    # Optional flag for edit
    parser_edit.add_argument('-m', '--model', help="New model name")
    parser_edit.add_argument('--idrac', action='store_true', help="Update iDRAC version")
    parser_edit.add_argument('--bios', action='store_true', help="Update BIOS version")

    # --- LOOKUP COMMAND ---
    parser_lookup = subparsers.add_parser('lookup', aliases=['l'], help="Lookup a system")
    lookup_group = parser_lookup.add_mutually_exclusive_group(required=True)
    lookup_group.add_argument('-s', '--svctag', help="Service tag to find")
    lookup_group.add_argument('-t', '--target', help="Target hostname to find")
    # Specific optional flags for lookup only
    parser_lookup.add_argument('--idrac', action='store_true', help="Print iDRAC version")
    parser_lookup.add_argument('--bios', action='store_true', help="Print BIOS version")
    parser_lookup.add_argument('--full', action='store_true', help="Print All fields")

    # --- LIST COMMAND ---
    parser_list = subparsers.add_parser('list', aliases=['li'], help="List systems")
    # Specific optional flags for list only
    parser_list.add_argument('-s', '--svctag', help="Service tag to find")
    parser_list.add_argument('-t', '--target', help="Target hostname to find")
    parser_list.add_argument('-m', '--model', help="Target model to list")
    parser_list.add_argument('--expires_in', help="List hosts that expire in N days")
    parser_list.add_argument('--json', action='store_true', help="Print list results in json format")
    parser_list.add_argument('--regex', help="Target hostname regex to list")
    # bios args
    list_bios_group = parser_list.add_mutually_exclusive_group(required=False)
    list_bios_group.add_argument('--bios_le', help="Target hostname with BIOS less than or equal to list")
    list_bios_group.add_argument('--bios_lt', help="Target hostname with BIOS less than to list")
    list_bios_group.add_argument('--bios_ge', help="Target hostname with BIOS greater than or equal to list")
    list_bios_group.add_argument('--bios_gt', help="Target hostname with BIOS greater than to list")
    list_bios_group.add_argument('--bios_eq', help="Target hostname with BIOS equal to to list")
    # idrac args
    list_idrac_group = parser_list.add_mutually_exclusive_group(required=False)
    list_idrac_group.add_argument('--idrac_le', help="Target hostname with iDRAC less than or equal to list")
    list_idrac_group.add_argument('--idrac_lt', help="Target hostname with iDRAC less than to list")
    list_idrac_group.add_argument('--idrac_ge', help="Target hostname with iDRAC greater than or equal to list")
    list_idrac_group.add_argument('--idrac_gt', help="Target hostname with iDRAC greater than to list")
    list_idrac_group.add_argument('--idrac_eq', help="Target hostname with iDRAC equal to to list")

    # --- REMOVE COMMAND ---
    parser_remove = subparsers.add_parser('remove', aliases=['r'], help="Remove a system")
    remove_group = parser_remove.add_mutually_exclusive_group(required=True)
    remove_group.add_argument('-s', '--svctag', help="Service tag to remove")
    remove_group.add_argument('-t', '--target', help="Target hostname to remove")

    args = parser.parse_args()

    # Handling Global Debug
    global debug
    debug = args.debug
    global debug_output
    debug_output = debug

    if args.svctag:
        target_tag = args.svctag.upper()
    else:
        target_tag = None

    if args.warranty:
        warranty = args.warranty
    else:
        warranty = str(Path(__file__).resolve().parent) + "/warranty.db"

    db_initialize(warranty)

    # Logic Routing
    if args.command in ['add', 'a']:
        await add_dell_warranty(target_tag, args.target, args.model, warranty)
    elif args.command in ['edit', 'e']:
        await edit_dell_warranty(target_tag, args.target, args.model, args.idrac, args.bios, warranty)
    elif args.command in ['lookup', 'l']:
        await lookup_dell_warranty(target_tag, args.target, args.idrac, args.bios, args.full, warranty)
    elif args.command in ['remove', 'r']:
        await remove_dell_warranty(target_tag, args.target, warranty)
    elif args.command in ['list', 'li']:
        await list_dell_warranty(target_tag, args.target, args.model, args.regex,
                                 args.bios_le, args.bios_lt, args.bios_ge, args.bios_gt, args.bios_eq,
                                 args.idrac_le, args.idrac_lt, args.idrac_ge, args.idrac_gt, args.idrac_eq,
                                 args.expires_in, args.json, warranty)

if __name__ == "__main__":
    load_dotenv()
    debug_output = False
    try:
        debug = os.environ['DEBUG']
        if debug == "true":
            debug_output = True
    except KeyError:
        debug_output = False
        debug = False

    asyncio.run(main())

