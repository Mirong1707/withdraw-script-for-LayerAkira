import argparse
import asyncio
import logging

from LayerAkira.src.CLIClient import CLIClient
from LayerAkira.src.hasher.Hasher import AppDomain


async def main():
    parser = argparse.ArgumentParser(prog='WithdrawScript',
                                     description='Automatic withdrawal of all funds from LayerAkira')
    parser.add_argument('--toml_config_file', default='config.toml')
    args = parser.parse_args()

    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO, filename='logs.txt')

    cli_client = CLIClient(args.toml_config_file)
    await cli_client.start(AppDomain(cli_client.cli_cfg.chain_id.value))


if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
