import argparse
import asyncio
import logging
import sys
import os
from contextlib import contextmanager

from LayerAkira.src.common.ContractAddress import ContractAddress
from LayerAkira.src.hasher.Hasher import AppDomain
from LayerAkira.src.common.ERC20Token import ERC20Token
from LayerAkira.src.common.common import precise_to_price_convert

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


class WithdrawClient(CustomCLIClient):
    
    async def withdraw_all_funds(self, domain):
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
        
        print("\n=== Getting balance information ===")
        with suppress_stdout():
            user_info = await self.handle_request(self.exchange_client, 'user_info', [], 
                                                trading_account, self.cli_cfg.gas_fee_steps)
        
        if user_info and hasattr(user_info, 'data'):
            balances = user_info.data.balances
            print("Current balances on exchange:")
            
            for token_symbol, (balance, locked) in balances.items():
                balance_float = float(balance) if balance != '0' else 0.0
                locked_float = float(locked) if locked != '0' else 0.0
                print(f"{token_symbol}: {balance_float:.6f} (locked: {locked_float:.6f})")
            
            print("\n=== Starting funds withdrawal ===")
            
            for token_symbol, (balance_raw, locked_raw) in balances.items():
                balance_float = float(balance_raw) if balance_raw != '0' else 0.0
                
                if balance_float > 0:
                    if token_symbol == 'STRK':
                        if balance_float > 1.0:
                            withdraw_amount = balance_float - 1.0
                            print(f"Withdrawing {token_symbol}: {withdraw_amount:.6f} (leaving 1 STRK)")
                        else:
                            print(f"Insufficient {token_symbol} for withdrawal (less than 1 token)")
                            continue
                    else:
                        withdraw_amount = balance_float
                        print(f"Withdrawing all {token_symbol}: {withdraw_amount:.6f}")
                    
                    try:
                        withdraw_amount_str = str(withdraw_amount)
                        
                        with suppress_stdout():
                            result = await self.handle_request(
                                self.exchange_client, 
                                'withdraw', 
                                [token_symbol, withdraw_amount_str], 
                                trading_account, 
                                self.cli_cfg.gas_fee_steps
                            )
                        print(f"Withdrawal result for {token_symbol}: {result}")
                        
                        await asyncio.sleep(2)
                        
                    except Exception as e:
                        print(f"Error withdrawing {token_symbol}: {e}")
                        logging.exception(e)
                else:
                    print(f"No funds to withdraw: {token_symbol}")
        
        else:
            print("Failed to get balance information")
        
        print("\n=== Funds withdrawal completed ===")
        
        # Wait 3 seconds and close connections
        print("Waiting 3 seconds before closing connections...")
        await asyncio.sleep(3)

async def main():
    parser = argparse.ArgumentParser(prog='WithdrawScript', description='Automatic withdrawal of all funds from LayerAkira')
    parser.add_argument('--toml_config_file', default='config.toml')
    args = parser.parse_args()
    
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO, filename='logs.txt')
    
    cli_client = WithdrawClient(args.toml_config_file)
    await cli_client.withdraw_all_funds(AppDomain(cli_client.cli_cfg.chain_id.value))


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main()) 