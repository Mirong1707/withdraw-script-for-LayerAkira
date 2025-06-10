import asyncio
import logging
from typing import List

from LayerAkira.src.AkiraExchangeClient import AkiraExchangeClient
from LayerAkira.src.CLIClient import CLIClient
from LayerAkira.src.HttpClient import AsyncApiHttpClient
from LayerAkira.src.JointHttpClient import JointHttpClient
from LayerAkira.src.WsClient import Stream, WsClient
from LayerAkira.src.common.ContractAddress import ContractAddress
from LayerAkira.src.common.ERC20Token import ERC20Token
from LayerAkira.src.common.TradedPair import TradedPair
from LayerAkira.src.hasher.Hasher import SnTypedPedersenHasher
from aioconsole import ainput
from starknet_py.hash.utils import message_signature
from starknet_py.net.full_node_client import FullNodeClient


class CustomCLIClient(CLIClient):

    async def start(self, domain):

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

        async def sub_consumer(d):
            logging.info(f'Subscription emitted {d}')

        async def handle_websocket_req(command: str, args: List[str]):
            if command == 'start_ws':
                asyncio.create_task(ws.run_stream_listener(ContractAddress(args[0]), True))
                return True
            elif command == 'subscribe_fills':
                print(await ws.subscribe_fills(ContractAddress(args[0]), sub_consumer))
                return True
            elif command == 'subscribe_book':
                print(await ws.subscribe_book(Stream(args[0]), TradedPair(ERC20Token(args[1]), ERC20Token(args[2])),
                                              bool(int(args[3])),
                                              sub_consumer))
                return True
            return False

        async def issue_listen_key(signer: ContractAddress):
            return (await self.exchange_client.query_listen_key(signer)).data

        ws = WsClient(self._erc_to_decimals, issue_listen_key, self.cli_cfg.wss, verbose=self.cli_cfg.verbose)
        trading_account = self.cli_cfg.trading_account[0]
        presets_commands = [
            ['set_account', self.cli_cfg.trading_account],
            # ['bind_to_signer', []],  # binds trading account to public key, can be invoked onlu once for trading account
            ['r_auth', []],  # issue jwt token

            ['display_chain_info', []],  # print chain info
            ['query_gas', []],  # query gas price
            ['user_info', []],  # query and ecosystem in Client user info from exchange
            ['start_ws', [self.cli_cfg.trading_account[1]]],
            ['sleep', []],
            # ['subscribe_book', ['trade', 'ETH', 'USDC', '1']],
            # ['subscribe_book', ['bbo', 'ETH', 'USDC', '1']],
            # ['subscribe_book', ['snap', 'ETH', 'USDC', '1']],
            ['subscribe_fills', [self.cli_cfg.trading_account[0]]],
            # ['approve_exchange', ['STRK', '1']],
            # deposit



        ]

        for command, args in presets_commands:
            try:
                if self.cli_cfg.verbose: logging.info(f'Executing {command} {args}')
                if not await handle_websocket_req(command, args):
                    print(await self.handle_request(self.exchange_client, command, args, trading_account,
                                                    self.cli_cfg.gas_fee_steps))
            except Exception as e:
                logging.exception(e)
        while True:
            try:
                request = await ainput(">>> ")
                args = request.split()
                if self.cli_cfg.verbose: logging.info(f'Executing {args[0].strip()} {args[1:]}')
                if not await handle_websocket_req(args[0].strip(), args[1:]):
                    print(await self.handle_request(self.exchange_client, args[0].strip(), args[1:], trading_account,
                                                    self.cli_cfg.gas_fee_steps))
            except Exception as e:
                logging.exception(e)