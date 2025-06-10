import argparse
import asyncio
import logging
import sys
import os
from contextlib import contextmanager

from LayerAkira.src.hasher.Hasher import AppDomain
from LayerAkira.src.common.ERC20Token import ERC20Token

from CustomCLIClient import CustomCLIClient


@contextmanager
def suppress_stdout():
    """Context manager to suppress stdout output from library code"""
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout


class BalanceChecker(CustomCLIClient):
    
    async def check_balances(self, domain):
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
        
        print("=== Connecting to LayerAkira ===")
        with suppress_stdout():
            await self.handle_request(self.exchange_client, 'set_account', self.cli_cfg.trading_account, 
                                    trading_account, self.cli_cfg.gas_fee_steps)
        
        with suppress_stdout():
            auth_result = await self.handle_request(self.exchange_client, 'r_auth', [], 
                                                  trading_account, self.cli_cfg.gas_fee_steps)
        
        if auth_result and hasattr(auth_result, 'data'):
            print("✅ Successful authorization")
        else:
            print("❌ Authorization failed")
            return
        
        print("\n=== LayerAkira Exchange Balances ===")
        with suppress_stdout():
            user_info = await self.handle_request(self.exchange_client, 'user_info', [], 
                                                trading_account, self.cli_cfg.gas_fee_steps)
        
        if user_info and hasattr(user_info, 'data'):
            balances = user_info.data.balances
            nonce = user_info.data.nonce
            
            print(f"Nonce: {nonce}")
            print("Balances:")
            
            total_value_found = False
            for token_symbol, (balance, locked) in balances.items():
                balance_float = float(balance) if balance != '0' else 0.0
                locked_float = float(locked) if locked != '0' else 0.0
                
                if balance_float > 0 or locked_float > 0:
                    print(f"  {token_symbol}: {balance_float:.6f} (locked: {locked_float:.6f})")
                    total_value_found = True
            
            if not total_value_found:
                print("  No funds on exchange")
        else:
            print("❌ Failed to get balance information on exchange")


async def main():
    parser = argparse.ArgumentParser(prog='BalanceChecker', description='Check balances on LayerAkira')
    parser.add_argument('--toml_config_file', default='config.toml')
    args = parser.parse_args()
    
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO, filename='logs.txt')
    
    cli_client = BalanceChecker(args.toml_config_file)
    await cli_client.check_balances(AppDomain(cli_client.cli_cfg.chain_id.value))


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main()) 