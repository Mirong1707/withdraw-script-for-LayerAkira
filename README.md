# LayerAkira Withdraw Script

Scripts for automatic withdrawal of all funds from LayerAkira exchange.

## Requirements

```bash
pip install -r requirements.txt
```

## Configuration

Make sure the following are properly configured in `config.toml`:

- `trading_account` - account address, public key and private key
- LayerAkira contract addresses
- Starknet node URL
- API settings

## Usage

### 1. Check Balances

Before withdrawing funds, it's recommended to check current balances:

```bash
python check_balances.py
```

This script will show:
- Current balances on LayerAkira exchange
- Locked funds
- Connection information

### 2. Automatic Withdrawal of All Funds

```bash
python withdraw.py
```

The script will perform the following actions:

1. **Connect to LayerAkira** - account setup and authorization
2. **Get Balances** - request information about all available funds
3. **Withdraw Funds**:
   - For all tokens (except STRK): withdraws all available funds
   - For STRK token: withdraws all funds minus 1 STRK (leaves 1 STRK on account)

### STRK Withdrawal 

The script automatically leaves 1 STRK token on the account to pay for future transactions. If the STRK balance is less than or equal to 1 token, no withdrawal is performed.

