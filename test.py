import asyncio
import aiohttp

import argparse
import datetime
import contextlib
import os

import prettytable

import aiosomecomfort


import logging

logging.basicConfig(level=logging.DEBUG)


async def get_or_set_things(client, args, device, settables, gettables):
    for thing in settables:
        value = getattr(args, "set_%s" % thing)
        if value is not None:
            setattr(device, thing, value)
            return 0

    for thing in gettables:
        isset = getattr(args, "get_%s" % thing)
        if isset:
            print(getattr(device, thing))
            return 0

    t = prettytable.PrettyTable(("Location", "Device", "Name"))
    for locid, location in client.locations_by_id.items():
        for devid, device in location.devices_by_id.items():
            t.add_row([locid, devid, device.name])
    print(t)


@contextlib.contextmanager
def persistent_session():
    statefile = os.path.join(os.path.expanduser("~"), ".somecomfort")
    data = {}
    # try:
    #     data = json.loads(open(statefile, "rb").read().decode())
    # except OSError:
    #     pass
    # except:
    #     print("Failed to load data store: %s" % statefile)

    session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=True))
    # session.cookie_jar.update(data.get("cookies", {}))
    try:
        yield session
    finally:
        # data = {
        #    "cookies": dict(session.cookie_jar.items()),
        # }
        # open(statefile, "wb").write(json.dumps(data).encode())
        # try:
        #    os.chmod(statefile, 0o600)
        # except OSError:
        pass


async def do_holds(client, args, device):
    if args.cancel_hold:
        await device.set_hold_heat(False)
        await device.set_hold_cool(False)
    elif args.permanent_hold:
        await device.set_hold_heat(True)
        await device.set_hold_cool(True)
    elif args.hold_until:
        try:
            holdtime = datetime.datetime.strptime(args.hold_until, "%H:%M")
        except ValueError:
            print("Invalid time (use HH:MM)")
            return False
        try:
            await device.hold_heat(holdtime.time())
            await device.hold_cool(holdtime.time())
        except client.SomeComfortError as ex:
            print("Failed to set hold: %s" % str(ex))
            return False
    elif args.get_hold:
        modes = {}
        for mode in ["cool", "heat"]:
            hold = getattr(device, "hold_%s" % mode)
            if hold is True:
                modes[mode] = "permanent"
            elif hold is False:
                modes[mode] = "schedule"
            else:
                modes[mode] = str(hold)
        print("heat:%s cool:%s" % (modes["heat"], modes["cool"]))
        return False
    return True


async def _main(session):
    number_things = ["setpoint_cool", "setpoint_heat","humidity"]
    string_things = ["fan_mode", "system_mode"]
    bool_things = ["cancel_hold", "permanent_hold"]
    settable_things = {float: number_things, str: string_things}
    readonly_things = [
        "current_temperature",
        "current_humidity",
        "outdoor_temperature",
        "outdoor_humidity",
        "equipment_output_status",
    ]
    parser = argparse.ArgumentParser()
    for thingtype, thinglist in settable_things.items():
        for thing in thinglist:
            parser.add_argument(
                "--get_%s" % thing,
                action="store_const",
                const=True,
                default=False,
                help="Get %s" % thing,
            )
            parser.add_argument(
                "--set_%s" % thing, type=thingtype, default=None, help="Set %s" % thing
            )
    for thing in readonly_things:
        parser.add_argument(
            "--get_%s" % thing,
            action="store_const",
            const=True,
            default=False,
            help="Get %s" % thing,
        )

    for thing in bool_things:
        parser.add_argument(
            "--%s" % thing,
            action="store_const",
            const=True,
            default=False,
            help="Set %s" % thing,
        )

    parser.add_argument(
        "--hold_until", type=str, default=None, help="Hold until time (HH:MM)"
    )
    parser.add_argument(
        "--get_hold",
        action="store_const",
        const=True,
        default=False,
        help="Get the current hold mode",
    )

    parser.add_argument("--username", help="username")
    parser.add_argument("--password", help="password")
    parser.add_argument("--device", help="device", type=int)
    parser.add_argument(
        "--login",
        help="Just try to login",
        action="store_const",
        const=True,
        default=False,
    )
    parser.add_argument(
        "--devices",
        help="List available devices",
        action="store_const",
        const=True,
        default=False,
    )
    parser.add_argument(
        "--loop",
        help="Loop on temperature and operating mode",
        action="store_const",
        const=True,
        default=False,
    )
    args = parser.parse_args()

    try:
        client = aiosomecomfort.AIOSomeComfort(
            args.username, args.password, session=session
        )
        await client.login()
        await client.discover()

    except aiosomecomfort.AuthError as ex:
        if not args.username and args.password:
            print("Login required and no credentials provided")
        else:
            print(str(ex))
        return 1

    if args.login:
        print("Success")
        return 0

    if args.devices:
        for l_name, location in client.locations_by_id.items():
            print("Location %s:" % l_name)
            for key, device in location.devices_by_id.items():
                print("  Device %s: %s" % (key, device.name))

    if not args.device:
        device = client.default_device
    else:
        device = client.get_device(args.device)

    if not device:
        print("Device not found")
        return 1

    if any([args.hold_until, args.cancel_hold, args.permanent_hold, args.get_hold]):
        cont = await do_holds(client, args, device)
        if not cont:
            return

    try:
        await get_or_set_things(
            client,
            args,
            device,
            number_things + string_things,
            number_things + string_things + readonly_things,
        )
    except aiosomecomfort.SomeComfortError as ex:
        print("%s: %s" % (ex.__class__.__name__, str(ex)))

    if args.loop:
        while True:
            await asyncio.sleep(15)
            # client.get_device(client.default_device)
            await device.refresh()
            print(getattr(device, "current_temperature"))
            print(getattr(device, "equipment_output_status"))


async def on_request_start(session, context, params):
    logging.getLogger("aiohttp.client").debug(f"Starting request <{params}>")


async def on_request_end(session, context, params):
    logging.getLogger("aiohttp.client").debug(f"Ending request <{params}>")
    logging.getLogger("aiohttp.client").debug(
        f"Ending request Sent Headers <{params.response.request_info.headers}>"
    )


async def on_request_chunk_send(session, context, params):
    logging.getLogger("aiohttp.client").debug(f"Request chunk sent <{params}>")


async def main():
    logging.basicConfig(level=logging.DEBUG)
    trace_config = aiohttp.TraceConfig()
    # trace_config.on_request_start.append(on_request_start)
    trace_config.on_request_headers_sent.append(on_request_start)
    trace_config.on_request_end.append(on_request_end)
    trace_config.on_request_chunk_sent.append(on_request_chunk_send)
    # async with aiohttp.ClientSession(
    #     connector=aiohttp.TCPConnector(ssl=False), trace_configs=[trace_config]
    # ) as session:
    async with aiohttp.ClientSession() as session:

        await _main(session)
        await session.close()


if __name__ == "__main__":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
