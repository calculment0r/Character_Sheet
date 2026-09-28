"""Relais TCP : écoute un port et renvoie tout vers une autre machine.

Le studio tourne sur DGX1, mais le Wi-Fi de DGX1 est accroché en 2,4 GHz
(28/09 : 135 ms d'aller-retour, 5 % de pertes) quand celui de DGX2 est en
5 GHz (9 ms, aucune perte). Lancé sur DGX2, ce relais sert la page de
DGX1 par le câble direct entre les deux machines :

  python3 tools/relay.py 8765 169.254.110.6:8765

puis http://192.168.10.247:8765/ depuis le PC. Rien à installer : la
bibliothèque standard suffit.
"""

import asyncio
import sys


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(1 << 16):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def main(port: int, host: str, target: int) -> None:
    async def handle(r_in: asyncio.StreamReader, w_in: asyncio.StreamWriter) -> None:
        try:
            r_out, w_out = await asyncio.open_connection(host, target)
        except OSError:
            w_in.close()
            return
        await asyncio.gather(pipe(r_in, w_out), pipe(r_out, w_in))

    server = await asyncio.start_server(handle, "0.0.0.0", port)
    print(f"relais :{port} → {host}:{target}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    dest_host, dest_port = sys.argv[2].rsplit(":", 1)
    asyncio.run(main(int(sys.argv[1]), dest_host, int(dest_port)))
