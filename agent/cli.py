"""`agent`: the command-line MCP client for Aide-commerce.

    agent login                      parent login (Cognito in aws mode)
    agent session start              create a session and get the first passport (delegated token)
    agent passport                   show the passport claims and expiry countdown
    agent tools                      tools/list through the gateway
    agent call add_to_cart --args '{"cart_id": "c1", "sku": "SKU-PEN-010", "quantity": 1, "merchant": "freshmart"}'
    agent shop "school supplies under $50" [--driver bedrock|scripted] [--scenario happy|hijacked|over_budget|stop]
    agent session stop               the Stop button, from the command line
    agent replay                     replay the saved (unexpired) token -> expect 401 after a stop
    agent demo reset
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time

import httpx

from agentpass.config import load_dotenv

from .broker import BrokerError, CredentialBroker
from .mcp_client import GatewayClient, GatewayError, ToolError
from .scripted import ScriptedShopper


def _broker(args: argparse.Namespace) -> CredentialBroker:
    return CredentialBroker(args.api)


def cmd_login(args: argparse.Namespace) -> int:
    broker = _broker(args)
    username = args.user or os.environ.get("AGENT_USER") or input("username: ")
    password = args.password or os.environ.get("AGENT_PASSWORD") or getpass.getpass("password: ")
    data = broker.login(username, password)
    print(f"logged in as {data['user']['username']} (sub {data['user']['sub']})")
    for mandate in data.get("mandates", []):
        print(
            f"mandate {mandate['mandate_id']}: ${mandate['max']:.2f} at {mandate['merchant']}, "
            f"ship to {mandate['ship_to_id']}, spent ${mandate['spent']:.2f}"
        )
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    _broker(args).clear()
    print("local agent state cleared")
    return 0


def cmd_session_start(args: argparse.Namespace) -> int:
    broker = _broker(args)
    session = broker.start_session(args.mandate)
    print(f"session {session['sid']} on mandate {session['mandate_id']} (agent {session['agent_id']})")
    _print_passport(broker)
    return 0


def cmd_session_stop(args: argparse.Namespace) -> int:
    session = _broker(args).stop()
    print(f"session {session['sid']} is now {session['status']} ({session.get('revoke_reason', '')})")
    return 0


def _print_passport(broker: CredentialBroker) -> None:
    claims = broker.claims
    if not claims:
        print("no passport yet")
        return
    print("Agent Passport (delegated token claims):")
    for key in ("iss", "sub", "aud", "agent_id", "sid", "mandate_id", "tool_profile", "merchant", "ship_to_id", "scope", "jti"):
        print(f"  {key:12} {claims.get(key)}")
    print(f"  {'act':12} {json.dumps(claims.get('act'))}")
    print(f"  {'expires in':12} {max(broker.seconds_to_expiry, 0)}s")


def cmd_passport(args: argparse.Namespace) -> int:
    broker = _broker(args)
    _print_passport(broker)
    try:
        session = broker.session()
    except BrokerError as exc:
        print(f"session: {exc.message}")
        return 0
    print(f"session {session['sid']}: {session['status']}")
    if session.get("budget"):
        b = session["budget"]
        print(f"budget: max ${b['max']:.2f} spent ${b['spent']:.2f} held ${b['held']:.2f} remaining ${b['remaining']:.2f}")
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    gateway = GatewayClient(_broker(args))
    gateway.initialize()
    for tool in gateway.list_tools():
        props = (tool.get("inputSchema") or {}).get("properties", {})
        print(f"- {tool['name']}({', '.join(props)}): {(tool.get('description') or '').splitlines()[0]}")
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    gateway = GatewayClient(_broker(args))
    try:
        result = gateway.call_tool(args.tool, json.loads(args.args))
    except GatewayError as exc:
        print(f"{exc.status} {exc.reason}: {exc.message}")
        if exc.data:
            print(json.dumps(exc.data, indent=2))
        return 1
    except ToolError as exc:
        print(f"store error: {exc}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


def cmd_shop(args: argparse.Namespace) -> int:
    broker = _broker(args)
    if not broker.sid:
        print("no session yet; starting one")
        broker.start_session(None)
    gateway = GatewayClient(broker)
    gateway.initialize()  # fetches a passport first if the session has none yet (e.g. a failed start)
    claims = broker.claims
    cart_id = args.cart or f"cart_{claims.get('sid', 'demo')}"
    driver = args.driver or os.environ.get("AGENT_DRIVER", "scripted")
    print(f"driver: {driver}")
    if driver == "bedrock":
        from .shopper import BedrockShopper

        budget = broker.mandate().get("remaining", 0.0)
        shopper = BedrockShopper(
            gateway,
            model_id=args.model or os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-5"),
            region=os.environ.get("AWS_REGION", "us-east-1"),
            cart_id=cart_id,
            merchant=claims["merchant"],
            ship_to_id=claims["ship_to_id"],
            budget=budget,
        )
        goal = args.goal or "Buy the school supplies on my list: notebooks, pens and glue. Stay under budget."
        print(f"goal: {goal}")
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

        try:
            print(shopper.run(goal))
        except NoCredentialsError:
            print(
                "error: no AWS credentials for Bedrock. Export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY "
                "(and AWS_SESSION_TOKEN), set AWS_PROFILE, or run `aws configure`. "
                "Without AWS use `--driver scripted` or `agent call search_products ...`.",
                file=sys.stderr,
            )
            return 3
        except (ClientError, BotoCoreError) as exc:
            print(f"error: Bedrock call failed: {exc}", file=sys.stderr)
            return 3
        return 0
    shopper = ScriptedShopper(
        gateway,
        cart_id=cart_id,
        merchant=claims["merchant"],
        ship_to_id=claims["ship_to_id"],
        step_delay=args.step_delay,
    )
    steps = shopper.run(args.scenario)
    outcomes = ", ".join(f"{s.tool}:{s.outcome}" for s in steps)
    print(f"== done: {outcomes}")
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    broker = _broker(args)
    token = broker.token_for_replay()
    print(f"replaying the saved passport (expires in {max(broker.seconds_to_expiry, 0)}s) against POST /mcp ...")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "view_cart", "arguments": {"cart_id": args.cart or "cart_replay"}}}
    resp = httpx.post(f"{broker.api_url}/mcp", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=20)
    data = resp.json()
    if "error" in data:
        err = data["error"]
        print(f"HTTP {resp.status_code}: {err.get('data', {}).get('reason')} - {err.get('message')}")
    else:
        print(f"HTTP {resp.status_code}: allowed")
    if args.expect and resp.status_code != args.expect:
        print(f"expected HTTP {args.expect}")
        return 1
    return 0


def cmd_token(args: argparse.Namespace) -> int:
    broker = _broker(args)
    token = broker.token_for_replay()
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(token)
        os.chmod(args.out, 0o600)
        print(f"token written to {args.out} (expires in {max(broker.seconds_to_expiry, 0)}s)")
    elif args.show:
        print(token)
    else:
        print("use --show to print the token or --out FILE to save it")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    for event in _broker(args).audit(args.after):
        print(f"{event['ts']} {event['decision']:7} {event['layer']:10} {event['tool']:24} {event['reason']}")
    return 0


def cmd_demo_reset(args: argparse.Namespace) -> int:
    data = _broker(args).reset_demo()
    print(f"reset ok (store reset: {data.get('store_reset')}); mandates: {[m['mandate_id'] for m in data.get('mandates', [])]}")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    import boto3

    region = os.environ.get("AWS_REGION", "us-east-1")
    bedrock = boto3.client("bedrock", region_name=region)
    print("inference profiles (use these ids for BEDROCK_MODEL_ID when listed):")
    for profile in bedrock.list_inference_profiles().get("inferenceProfileSummaries", []):
        print(f"  {profile['inferenceProfileId']}")
    print("foundation models with text output:")
    for model in bedrock.list_foundation_models(byOutputModality="TEXT").get("modelSummaries", []):
        print(f"  {model['modelId']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent", description="Aide-commerce shopping agent (MCP client)")
    parser.add_argument("--api", default=os.environ.get("AGENTPASS_API_URL", "http://127.0.0.1:8000"))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("login", help="parent login")
    p.add_argument("--user")
    p.add_argument("--password")
    p.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="forget local state").set_defaults(func=cmd_logout)

    session = sub.add_parser("session", help="session lifecycle").add_subparsers(dest="action", required=True)
    p = session.add_parser("start")
    p.add_argument("--mandate")
    p.set_defaults(func=cmd_session_start)
    session.add_parser("stop").set_defaults(func=cmd_session_stop)
    session.add_parser("status").set_defaults(func=cmd_passport)

    sub.add_parser("passport", help="show the Agent Passport").set_defaults(func=cmd_passport)
    sub.add_parser("tools", help="tools/list through the gateway").set_defaults(func=cmd_tools)

    p = sub.add_parser("call", help="call one MCP tool through the gateway")
    p.add_argument("tool")
    p.add_argument("--args", default="{}", help="JSON arguments")
    p.set_defaults(func=cmd_call)

    p = sub.add_parser("shop", help="run the shopping agent")
    p.add_argument("goal", nargs="?")
    p.add_argument("--driver", choices=("bedrock", "scripted"))
    p.add_argument("--scenario", default="happy", choices=("happy", "hijacked", "over_budget", "stop"))
    p.add_argument("--cart")
    p.add_argument("--model")
    p.add_argument("--step-delay", type=float, default=0.0)
    p.set_defaults(func=cmd_shop)

    p = sub.add_parser("replay", help="replay the saved delegated token")
    p.add_argument("--cart")
    p.add_argument("--expect", type=int)
    p.set_defaults(func=cmd_replay)

    p = sub.add_parser("token", help="export the delegated token for the curl replay demo")
    p.add_argument("--show", action="store_true")
    p.add_argument("--out")
    p.set_defaults(func=cmd_token)

    p = sub.add_parser("audit", help="print the session's audit events")
    p.add_argument("--after")
    p.set_defaults(func=cmd_audit)

    demo = sub.add_parser("demo", help="demo helpers").add_subparsers(dest="action", required=True)
    demo.add_parser("reset").set_defaults(func=cmd_demo_reset)

    sub.add_parser("models", help="list Bedrock models in the account").set_defaults(func=cmd_models)
    return parser


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = build_parser().parse_args(argv)
    started = time.perf_counter()
    try:
        code = args.func(args)
    except BrokerError as exc:
        print(f"error: {exc.reason}: {exc.message}", file=sys.stderr)
        code = 1
    except httpx.HTTPError as exc:
        print(f"error: cannot reach {args.api}: {exc}", file=sys.stderr)
        code = 2
    if os.environ.get("AGENT_TIMING"):
        print(f"({time.perf_counter() - started:.2f}s)")
    sys.exit(code)


if __name__ == "__main__":
    main()
