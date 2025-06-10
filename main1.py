import argparse
import asyncio
import logging

from LayerAkira.src.hasher.Hasher import AppDomain

from CustomCLIClient import CustomCLIClient

if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='CliClient', description='Interact with LayerAkira')
    parser.add_argument('--toml_config_file', default='config.toml')
    args = parser.parse_args()
    logging.basicConfig(format='%(asctime)s %(message)s', level=logging.INFO, filename='logs.txt')


    async def main():
        cli_client = CustomCLIClient(args.toml_config_file)
        await cli_client.start(AppDomain(cli_client.cli_cfg.chain_id.value))


    asyncio.get_event_loop().run_until_complete(main())
