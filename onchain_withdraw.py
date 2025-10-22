import argparse
import asyncio
import logging
import os
import sys
from contextlib import contextmanager
from decimal import Decimal

from LayerAkira.src.common.ContractAddress import ContractAddress
from LayerAkira.src.common.ERC20Token import ERC20Token
from LayerAkira.src.hasher.Hasher import AppDomain
from LayerAkira.src.common.Requests import Withdraw, GasFee, SignScheme

from CustomCLIClient import CustomCLIClient


@contextmanager
def suppress_stdout():
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout


class OnChainWithdrawClient(CustomCLIClient):

    async def check_and_withdraw_onchain_balances(self, domain):
        from starknet_py.net.full_node_client import FullNodeClient
        from LayerAkira.src.AkiraExchangeClient import AkiraExchangeClient
        from LayerAkira.src.HttpClient import AsyncApiHttpClient
        from LayerAkira.src.JointHttpClient import JointHttpClient
        from LayerAkira.src.hasher.Hasher import SnTypedPedersenHasher
        from starknet_py.hash.utils import message_signature

        node_client = FullNodeClient(node_url=self.cli_cfg.node)
        erc_to_addr = {token.symbol: token.address for token in self.cli_cfg.tokens}
        contract_client = AkiraExchangeClient(node_client,
                                              self.cli_cfg.core_address,
                                              self.cli_cfg.executor_address,
                                              self.cli_cfg.router_address,
                                              self.cli_cfg.snip9_address,
                                              erc_to_addr)
        await contract_client.init()

        sn_hasher = SnTypedPedersenHasher(erc_to_addr, domain, self.cli_cfg.core_address,
                                          self.cli_cfg.executor_address)
        api_client = AsyncApiHttpClient(sn_hasher, lambda msg_hash, pk: message_signature(msg_hash, pk),
                                        self._erc_to_decimals, self.cli_cfg.http,
                                        verbose=self.cli_cfg.verbose)

        self.exchange_client = JointHttpClient(node_client, api_client, contract_client,
                                               self.cli_cfg.core_address,
                                               self.cli_cfg.executor_address,
                                               self.cli_cfg.invoker_address,
                                               erc_to_addr,
                                               self._erc_to_decimals,
                                               self.cli_cfg.chain_id,
                                               self.cli_cfg.gas_multiplier,
                                               verbose=self.cli_cfg.verbose)

        await self.exchange_client.init()

        trading_account = self.cli_cfg.trading_account[0]

        print("=== Setting up account ===")
        with suppress_stdout():
            await self.handle_request(self.exchange_client, 'set_account', self.cli_cfg.trading_account,
                                      trading_account, self.cli_cfg.gas_fee_steps)

        print("=== Checking signer binding ===")
        try:
            signer_result = await contract_client.get_signer(trading_account)
            current_signer: ContractAddress = signer_result.data if hasattr(signer_result, 'data') else signer_result
            print(current_signer)

            if current_signer.as_int() == 0 or current_signer is None:
                print("Signer not bound, binding to signer...")
                with suppress_stdout():
                    bind_result = await self.handle_request(self.exchange_client, 'bind_to_signer', [],
                                                            trading_account, self.cli_cfg.gas_fee_steps)
                print(f"Bind result: {bind_result}")
            else:
                print(f"Signer already bound: {current_signer}")
        except Exception as e:
            print(f"Error checking signer: {e}")
            logging.exception(e)

        print("=== Authorization ===")
        with suppress_stdout():
            auth_result = await self.handle_request(self.exchange_client, 'r_auth', [],
                                                    trading_account, self.cli_cfg.gas_fee_steps)
        print(f"Authorization: {auth_result}")

        print("=== Updating gas price ===")
        try:
            with suppress_stdout():
                gas_result = await self.handle_request(self.exchange_client, 'query_gas_price', [],
                                                       trading_account, self.cli_cfg.gas_fee_steps)
            print(f"Gas price updated: {gas_result}")
        except Exception as e:
            print(f"Error updating gas price: {e}")
            logging.exception(e)

        print("\n=== Checking on-chain balances ===")

        # Refresh chain info to get on-chain balances
        try:
            with suppress_stdout():
                chain_info_result = await self.handle_request(
                    self.exchange_client,
                    'refresh_chain_info',
                    [],
                    trading_account,
                    self.cli_cfg.gas_fee_steps
                )

            print("Chain info refreshed successfully")

        except Exception as e:
            print(f"Error refreshing chain info: {e}")
            logging.exception(e)
            print("Waiting 3 seconds before closing connections...")
            await asyncio.sleep(3)
            return

        # Get on-chain balances from chain info
        onchain_balances = {}
        onchain_balances_raw = {}  # Store raw values for withdrawal
        tokens_with_balance = []

        if chain_info_result:
            # chain_info_result is a tuple: (nonce, balances_dict, signer_address)
            if isinstance(chain_info_result, tuple) and len(chain_info_result) >= 2:
                nonce, balances_dict, signer_address = chain_info_result

                print(f"Nonce: {nonce}")
                print(f"Signer: {signer_address}")
                print("On-chain balances:")

                for token_symbol, balance_raw in balances_dict.items():
                    # Store raw balance for withdrawal
                    onchain_balances_raw[token_symbol] = balance_raw

                    # Convert from raw balance to human readable using Decimal for precision
                    if token_symbol in self._erc_to_decimals:
                        decimals = self._erc_to_decimals[token_symbol]
                        balance_decimal = Decimal(str(balance_raw)) / Decimal(10 ** decimals)
                    else:
                        balance_decimal = Decimal(str(balance_raw))

                    onchain_balances[token_symbol] = balance_decimal

                    if balance_decimal > 0:
                        tokens_with_balance.append(token_symbol)
                        # Format decimal without scientific notation
                        balance_str = f"{balance_decimal:.30f}".rstrip('0').rstrip('.')
                        print(f"  {token_symbol}: {balance_str}")
                    else:
                        print(f"  {token_symbol}: 0")
            else:
                print(f"Unexpected chain info result format: {chain_info_result}")
        else:
            print(f"Failed to get chain info, result: {chain_info_result}")

        if not tokens_with_balance:
            print("\nNo on-chain balances found.")
            print("Waiting 3 seconds before closing connections...")
            await asyncio.sleep(3)
            return

        print(f"\nFound balances for: {', '.join(tokens_with_balance)}")

        # Ask user if they want to withdraw
        while True:
            try:
                # break
                user_input = input("\nDo you want to withdraw all on-chain balances? (y/n): ").strip().lower()
                if user_input in ['y', 'yes', 'да', 'д']:
                    break
                elif user_input in ['n', 'no', 'нет', 'н']:
                    print("Withdrawal cancelled.")
                    print("Waiting 3 seconds before closing connections...")
                    await asyncio.sleep(3)
                    return
                else:
                    print("Please enter 'y' for yes or 'n' for no.")
            except KeyboardInterrupt:
                print("\nOperation cancelled by user.")
                return

        print("\n=== Starting on-chain withdrawal process ===")

        # Step 1: Request withdrawal for all tokens with balance
        withdrawal_requests = []

        for token_symbol in tokens_with_balance:
            balance_human = onchain_balances[token_symbol]
            
            # For STRK, leave 1 token on balance
            if token_symbol == 'STRK':
                if balance_human <= Decimal('1.0'):
                    print(f"Skipping {token_symbol}: insufficient balance for withdrawal (need > 1 STRK, have {balance_human})")
                    continue
                withdraw_amount = balance_human - Decimal('1.0')
            else:
                withdraw_amount = balance_human
            
            # Format human readable amount for display and API
            balance_str = f"{withdraw_amount:.30f}".rstrip('0').rstrip('.')
            print(f"Requesting withdrawal for {token_symbol}: {balance_str}")

            try:
                with suppress_stdout():
                    request_result = await self.handle_request(
                        self.exchange_client,
                        'request_withdraw_on_chain',
                        [token_symbol, balance_str],  # Use human readable amount as string
                        trading_account,
                        self.cli_cfg.gas_fee_steps
                    )

                if request_result:
                    withdrawal_requests.append((token_symbol, withdraw_amount, request_result))
                    print(f"✅ Withdrawal request for {token_symbol}: {request_result}")
                else:
                    print(f"❌ Failed to request withdrawal for {token_symbol} res {request_result}")

                await asyncio.sleep(2)

            except Exception as e:
                error_str = str(e)
                print(f"Error requesting withdrawal for {token_symbol}: {e}")
                logging.exception(e)

                # Check if error is about previous withdraw not completed
                if "NOT_YET_COMPLETED_PREV" in error_str or "previous withdraw has not been completed yet" in error_str:
                    print(
                        f"⚠️ Previous withdrawal for {token_symbol} not completed yet. Trying to get pending withdrawal key...")
                    logging.info(
                        f"Previous withdrawal for {token_symbol} not completed, getting pending withdrawal key")

                    try:
                        # Get token address for the contract call
                        token_address = None
                        for token_config in self.cli_cfg.tokens:
                            if token_config.symbol == token_symbol:
                                token_address = token_config.address
                                break

                        if token_address:
                            # Get pending withdrawal key
                            pending_result = await contract_client.get_pending_withdraw(trading_account, token_address)

                            if pending_result and hasattr(pending_result, 'data') and pending_result.data:
                                pending_key = pending_result.data
                                print(f"📋 Found pending withdrawal key for {token_symbol}: {pending_key}")
                                logging.info(f"Found pending withdrawal key for {token_symbol}: {pending_key}")

                                # Add to withdrawal requests with pending key
                                withdrawal_requests.append((token_symbol, withdraw_amount, pending_key))
                            else:
                                print(f"❌ Could not get pending withdrawal key for {token_symbol}: {pending_result}")
                                logging.warning(
                                    f"Could not get pending withdrawal key for {token_symbol}: {pending_result}")
                        else:
                            print(f"❌ Could not find token address for {token_symbol}")
                            logging.warning(f"Could not find token address for {token_symbol}")

                    except Exception as pending_error:
                        print(f"❌ Error getting pending withdrawal for {token_symbol}: {pending_error}")
                        logging.exception(f"Error getting pending withdrawal for {token_symbol}: {pending_error}")

        if not withdrawal_requests:
            print("No successful withdrawal requests.")
            print("Waiting 3 seconds before closing connections...")
            await asyncio.sleep(3)
            return

        print(f"\n=== Applying {len(withdrawal_requests)} withdrawal(s) ===")

        # Step 2: Apply on-chain withdrawals
        for token_symbol, amount, request_result in withdrawal_requests:
            # Format amount without scientific notation
            amount_str = f"{amount:.30f}".rstrip('0').rstrip('.')
            print(f"Applying withdrawal for {token_symbol}: {amount_str}")

            try:
                # Extract withdrawal key from request_result
                withdrawal_key = None

                if isinstance(request_result, tuple) and len(request_result) == 2:
                    # request_result is a tuple of (block_info, withdraw_data)
                    block_info, withdraw_data = request_result

                    # Create Withdraw object from the data

                    # Extract data from withdraw_data OrderedDict
                    maker = ContractAddress(withdraw_data['maker'])
                    token_addr = ContractAddress(withdraw_data['token'])
                    amount = withdraw_data['amount']
                    salt = withdraw_data['salt']
                    gas_fee_data = withdraw_data['gas_fee']
                    receiver = ContractAddress(withdraw_data['receiver'])
                    sign_scheme = SignScheme.NOT_SPECIFIED

                    def get_token_by_address(adr: ContractAddress):
                        for tc in self.cli_cfg.tokens:
                            if tc.address == adr:
                                return ERC20Token(tc.symbol)
                        raise Exception(f"Could not find token address for {token_addr}")


                    # Create GasFee object
                    print(f"DEBUG: gas_fee_data = {gas_fee_data}")
                    gas_fee = GasFee(
                        gas_per_action=gas_fee_data['gas_per_action'],
                        fee_token=get_token_by_address(ContractAddress(gas_fee_data['fee_token'])),
                        max_gas_price=gas_fee_data['max_gas_price'],
                        conversion_rate=gas_fee_data['conversion_rate']
                    )

                    # Find ERC20Token by address
                    token_obj = get_token_by_address(token_addr)

                    if token_obj:
                        # Create Withdraw object
                        withdraw = Withdraw(
                            maker=maker,
                            token=token_obj,
                            amount=amount,
                            salt=salt,
                            sign=(0, 0),
                            gas_fee=gas_fee,
                            receiver=receiver,
                            sign_scheme=sign_scheme
                        )
                        
                        print(withdraw_data)
                        print(withdraw)

                        # Calculate withdrawal key using hasher
                        withdrawal_key = hex(sn_hasher.hash(withdraw))
                        print(f"Calculated withdrawal key: {withdrawal_key}")
                    else:
                        print(f"❌ Could not find token config for address {token_addr}")
                        continue

                elif hasattr(request_result, 'data'):
                    withdrawal_key = request_result.data
                elif isinstance(request_result, str):
                    withdrawal_key = request_result
                else:
                    withdrawal_key = str(request_result)
                    print(f"Using withdrawal key as string: {withdrawal_key}")

                while True:
                    try:
                        with suppress_stdout():
                            apply_result = await self.handle_request(
                                self.exchange_client,
                                'apply_onchain_withdraw',
                                [token_symbol, withdrawal_key],  # Pass token and withdrawal key
                                trading_account,
                                self.cli_cfg.gas_fee_steps
                            )
                        if apply_result:
                            print(f"✅ Applied withdrawal for {token_symbol}: {apply_result}")
                            break
                        else:
                            print(f"❌ Failed to apply withdrawal for {token_symbol} res {apply_result}")
                            break
                        await asyncio.sleep(3)
                    except Exception as e:
                        error_str = str(e)
                        print(f"Error applying withdrawal for {token_symbol}: {e}")
                        logging.exception(e)
                        if "FEW_TIME_PASSED" in error_str:
                            import re
                            import time
                            
                            # Parse from error: "wait at least X block and Y ts (for now its block_delta and ts_delta)"
                            # block_delta and ts_delta - how much time has already passed
                            delta_match = re.search(r'\(for now its (\d+) and (\d+)\)', error_str)
                            
                            if delta_match:
                                block_delta = int(delta_match.group(1))
                                ts_delta = int(delta_match.group(2))
                                
                                # Limits from config
                                limit_block = 2
                                limit_ts = 60
                                
                                # Calculate how much MORE we need to wait
                                remaining_blocks = max(0, limit_block - block_delta)
                                remaining_seconds = max(0, limit_ts - ts_delta)
                                
                                print(f"⏰ Already passed: {block_delta}/{limit_block} blocks, {ts_delta}/{limit_ts} seconds")
                                print(f"⏰ Need to wait: {remaining_blocks} more blocks AND {remaining_seconds} more seconds")
                                
                                if remaining_blocks > 0 or remaining_seconds > 0:
                                    # Wait until enough time passes
                                    start_block = await node_client.get_block_number()
                                    start_ts = int(time.time())
                                    
                                    target_block = start_block + remaining_blocks
                                    target_ts = start_ts + remaining_seconds
                                    
                                    while True:
                                        current_block = await node_client.get_block_number()
                                        current_ts = int(time.time())
                                        
                                        blocks_left = max(0, target_block - current_block)
                                        seconds_left = max(0, target_ts - current_ts)
                                        
                                        if blocks_left == 0 and seconds_left == 0:
                                            print(f"✅ Wait complete! Waited {remaining_blocks} blocks and {remaining_seconds} seconds")
                                            break
                                        
                                        print(f"⏰ Waiting... {blocks_left} blocks, {seconds_left} seconds left")
                                        await asyncio.sleep(10)
                                
                                print(f"🔄 Retrying withdrawal application for {token_symbol}")
                                continue  # retry apply_onchain_withdraw
                            else:
                                print(f"❌ Could not parse block/timestamp from error")
                                logging.warning(f"Could not parse block/timestamp from error: {error_str}")
                                break
                        else:
                            break
                    await asyncio.sleep(3)

            except Exception as e:
                print(f"❌ Error processing withdrawal for {token_symbol}: {e}")
                logging.exception(f"Error processing withdrawal for {token_symbol}: {e}")

        print("\n=== On-chain withdrawal process completed ===")
        print("Note: On-chain withdrawals may take some time to be processed on the blockchain.")

        print("Waiting 3 seconds before closing connections...")
        await asyncio.sleep(3)


async def main():
    parser = argparse.ArgumentParser(prog='OnChainWithdrawScript',
                                     description='Check and withdraw on-chain balances from LayerAkira')
    parser.add_argument('--toml_config_file', default='config.toml')
    args = parser.parse_args()

    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO, filename='logs.txt')

    cli_client = OnChainWithdrawClient(args.toml_config_file)
    await cli_client.check_and_withdraw_onchain_balances(AppDomain(cli_client.cli_cfg.chain_id.value))


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
