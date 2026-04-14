"""
Main CLI entry point for Research Platform.
"""

import click
from rich.console import Console

from src.cli import __version__
from src.cli.commands.agents import agents_group
from src.cli.commands.projects import projects_group
from src.cli.config import config
from src.cli.formatters import OutputFormatter


@click.group()
@click.version_option(version=__version__, prog_name="research-cli")
@click.option(
    "--api-url",
    envvar="RESEARCH_API_URL",
    help="API base URL",
)
@click.option(
    "--format",
    "-f",
    type=click.Choice(["table", "json", "yaml", "csv"]),
    default="table",
    help="Output format",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.option(
    "--no-color",
    is_flag=True,
    help="Disable colored output",
)
@click.pass_context
def cli(ctx, api_url: str, format: str, verbose: bool, no_color: bool):
    """Research Platform CLI - Manage research projects from the command line."""
    # Update config with command line options
    if api_url:
        config.api_url = api_url

    if format:
        config.output_format = format

    if verbose:
        config.verbose = True

    if no_color:
        config.color = False

    # Create formatter
    formatter = OutputFormatter(
        format_type=config.output_format,
        color=config.color,
    )

    # Store in context for subcommands
    ctx.ensure_object(dict)
    ctx.obj["config"] = config
    ctx.obj["formatter"] = formatter
    ctx.obj["verbose"] = config.verbose
    ctx.obj["console"] = Console(no_color=no_color)


@cli.command(name="config")
@click.argument("action", type=click.Choice(["show", "set", "save"]))
@click.argument("key", required=False)
@click.argument("value", required=False)
@click.pass_context
def config_command(ctx, action: str, key: str, value: str):
    """Manage CLI configuration."""
    cli_config = ctx.obj["config"]

    if action == "show":
        # Show configuration
        if key:
            # Show specific key
            if hasattr(cli_config, key):
                click.echo(f"{key}: {getattr(cli_config, key)}")
            else:
                click.echo(f"Unknown configuration key: {key}")
        else:
            # Show all configuration
            click.echo("Current Configuration:")
            for field_name, field_value in cli_config.model_dump().items():
                if field_value is not None:
                    click.echo(f"  {field_name}: {field_value}")

    elif action == "set":
        # Set configuration value
        if not key or not value:
            click.echo("Usage: research-cli config set <key> <value>")
            raise click.Exit(1)

        if not hasattr(cli_config, key):
            click.echo(f"Unknown configuration key: {key}")
            raise click.Exit(1)

        # Update configuration
        try:
            # Handle boolean values
            if key in ["verbose", "color"]:
                value = value.lower() in ["true", "1", "yes"]
            # Handle integer values
            elif key in ["api_timeout", "max_retries"]:
                value = int(value)

            setattr(cli_config, key, value)
            click.echo(f"Set {key} = {value}")
        except Exception as e:
            click.echo(f"Error setting configuration: {e}")
            raise click.Exit(1)

    elif action == "save":
        # Save configuration to file
        try:
            cli_config.save_to_file()
            click.echo("Configuration saved to ~/.research-cli.env")
        except Exception as e:
            click.echo(f"Error saving configuration: {e}")
            raise click.Exit(1)


@cli.command(name="health")
@click.pass_context
def health_check(ctx):
    """Check API health status."""
    import asyncio

    from src.cli.client import APIError, ResearchAPIClient
    from src.cli.formatters import print_error, print_success

    verbose = ctx.obj["verbose"]

    async def _check_health():
        async with ResearchAPIClient(verbose=verbose) as client:
            try:
                # Check health
                health = await client.health_check()
                print_success(f"API is healthy: {health['status']}")

                # Check readiness
                ready = await client.readiness_check()
                print_success(f"API is ready: {ready['status']}")

                if verbose:
                    click.echo("\nService checks:")
                    for service, status in ready.get("checks", {}).items():
                        symbol = "✓" if status == "ok" else "✗"
                        click.echo(f"  {symbol} {service}: {status}")

            except APIError as e:
                print_error(f"API health check failed: {e.detail}")
                raise click.Exit(1)
            except Exception as e:
                print_error(f"Failed to connect to API: {e}")
                raise click.Exit(1)

    asyncio.run(_check_health())


# Add command groups
cli.add_command(agents_group)
cli.add_command(projects_group)


# Shell completion
@cli.command(name="completion")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def show_completion(shell: str):
    """Show shell completion script."""
    if shell == "bash":
        click.echo(
            """
# Add this to your ~/.bashrc or ~/.bash_profile:
eval "$(_RESEARCH_CLI_COMPLETE=bash_source research-cli)"
        """
        )
    elif shell == "zsh":
        click.echo(
            """
# Add this to your ~/.zshrc:
eval "$(_RESEARCH_CLI_COMPLETE=zsh_source research-cli)"
        """
        )
    elif shell == "fish":
        click.echo(
            """
# Add this to your ~/.config/fish/config.fish:
eval (env _RESEARCH_CLI_COMPLETE=fish_source research-cli)
        """
        )


if __name__ == "__main__":
    cli()
