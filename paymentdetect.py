import requests
import json
import time
from datetime import datetime

# Firebase configuration
const firebaseConfig = {
  apiKey: "AIzaSyA8-C1UPfTxsE0eQ1ob28ErBjgpUJgy2j0",
  authDomain: "cyber-86dca.firebaseapp.com",
  databaseURL: "https://cyber-86dca-default-rtdb.firebaseio.com",
  projectId: "cyber-86dca",
  storageBucket: "cyber-86dca.firebasestorage.app",
  messagingSenderId: "941786531248",
  appId: "1:941786531248:web:699e731c967d231417c275",
  measurementId: "G-546903W9CP"
};

# Firebase Realtime Database URL
database_url = firebase_config['databaseURL']

# Supported cryptocurrencies
SUPPORTED_CURRENCIES = ['BTC', 'ETH', 'USDT']

# API endpoints for checking transaction details
API_URLS = {
    'BTC': "https://api.blockcypher.com/v1/btc/main/txs/",
    'ETH': "https://api.etherscan.io/api?module=transaction&action=gettxreceiptstatus&txhash=",
    'USDT': "https://api.tronscan.org/api/transaction?hash="
}

# Function to fetch data from Firebase Realtime Database
def fetch_data(path):
    try:
        response = requests.get(f"{database_url}/{path}.json")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error fetching data: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Exception occurred: {e}")
    return None

# Function to write data to Firebase Realtime Database
def write_data(path, data):
    try:
        response = requests.patch(f"{database_url}/{path}.json", json=data)
        if response.status_code == 200:
            print(f"Data written successfully to {path}")
        else:
            print(f"Error writing data: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Exception occurred: {e}")

# Function to verify wallet address
def verify_wallet(currency, wallet_address):
    if currency == "USDT" and wallet_address.startswith("T"):
        return True
    elif currency == "ETH" and wallet_address.startswith("0x"):
        return True
    elif currency == "BTC" and len(wallet_address) in [34, 42]:
        return True
    return False

# Function to scan payment for a specific cryptocurrency
def scan_payment(currency, txid):
    try:
        url = API_URLS.get(currency)
        if not url:
            print(f"Unsupported currency: {currency}")
            return None

        response = requests.get(f"{url}{txid}")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error scanning payment: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Exception in scan_payment: {e}")
    return None

# Function to verify the transaction recipient and amount
def verify_transaction(currency, tx_data, wallet_address, expected_amount):
    received_amount = 0.0

    if currency == 'BTC':
        for output in tx_data.get('outputs', []):
            if wallet_address in output.get('addresses', []):
                received_amount += output.get('value', 0)
        received_amount /= 1e8  # Convert satoshi to BTC

    elif currency == 'ETH':
        if tx_data.get('to') == wallet_address:
            received_amount = int(tx_data.get('value', '0'), 16) / 1e18

    elif currency == 'USDT':
        transfer_info = tx_data.get('transferInfo', [])
        for transfer in transfer_info:
            if transfer.get('to_address') == wallet_address:
                received_amount += float(transfer.get('amount', 0))

    return received_amount >= expected_amount, received_amount

# Update payment status based on payment check
def update_status_if_new(path):
    data = fetch_data(path)
    if not data:
        print("No payment requests found. Retrying...")
        return

    for payment_id, payment_data in data.items():
        status = payment_data.get("status")
        txid = payment_data.get("txid")
        wallet_address = payment_data.get("walletAddress")
        currency = payment_data.get("currency", "BTC")
        expected_amount = float(payment_data.get("expectedAmount", 0))

        if currency not in SUPPORTED_CURRENCIES:
            print(f"Payment ID {payment_id}: Unsupported currency {currency}. Skipping.")
            continue

        if not verify_wallet(currency, wallet_address):
            print(f"Payment ID {payment_id}: Invalid wallet address {wallet_address}. Setting status to failed.")
            payment_data["status"] = "failed"
            write_data(f"{path}/{payment_id}", payment_data)
            continue

        if status == "processing" and txid:
            transaction_info = scan_payment(currency, txid)
            if transaction_info:
                valid, received_amount = verify_transaction(currency, transaction_info, wallet_address, expected_amount)
                if valid:
                    payment_data["status"] = "completed"
                    print(f"Payment ID {payment_id}: Payment verified. Received: {received_amount}")
                else:
                    payment_data["status"] = "failed"
                    print(f"Payment ID {payment_id}: Payment verification failed. Received: {received_amount}, Expected: {expected_amount}")
            else:
                payment_data["status"] = "failed"
                print(f"Payment ID {payment_id}: Transaction not found or error in scanning.")
            write_data(f"{path}/{payment_id}", payment_data)

if __name__ == "__main__":
    while True:
        print("Checking for new payment requests...")
        update_status_if_new('payments')
        time.sleep(5)