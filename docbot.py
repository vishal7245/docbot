import discord
from discord.ext import commands
import asyncio
import json

# Load configuration
try:
    with open('config.json', 'r') as f:
        config = json.load(f)
except FileNotFoundError:
    config = {
        'token': 'YOUR_BOT_TOKEN_HERE',
        'guilds': {}  # { guild_id: { "text_channel_id": id, "voice_channels": [ids] } }
    }

intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)
bot.remove_command('help')  # Remove default help command

# Store warnings per guild: { guild_id: { user_id: {"warning_msg": <Message>, "timer": <Task>} } }
guild_warnings = {}

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')

@bot.event
async def on_voice_state_update(member, before, after):
    """Monitors voice state changes for camera off/on handling."""
    guild_id_str = str(member.guild.id)
    
    # Skip if guild not configured or if it's a bot account
    if guild_id_str not in config['guilds'] or member.bot:
        return

    # Ensure a warnings dict exists for this guild
    if guild_id_str not in guild_warnings:
        guild_warnings[guild_id_str] = {}

    guild_config = config['guilds'][guild_id_str]
    voice_channels = guild_config.get('voice_channels', [])
    text_channel_id = guild_config.get('text_channel_id')

    # Skip if no channels configured
    if not voice_channels or not text_channel_id:
        return

    text_channel = bot.get_channel(text_channel_id)

    # Check if the channel is one we're monitoring
    is_monitored_channel = lambda channel: channel and channel.id in voice_channels

    # --- Join designated voice channel ---
    if is_monitored_channel(after.channel):
        if member.voice and not after.mute:
            try:
                await member.edit(mute=True)
            except discord.errors.HTTPException:
                print(f"Failed to mute {member.name} in {member.guild.name}")
        
        if not after.self_video:
            await send_warning(member, text_channel, guild_id_str)

    # --- Leave designated voice channel ---
    elif is_monitored_channel(before.channel) and not is_monitored_channel(after.channel):
        await cancel_warning(member, guild_id_str)

    # --- Turn camera on in designated channel ---
    if (is_monitored_channel(before.channel)
        and not before.self_video
        and after.self_video
    ):
        if member.id in guild_warnings[guild_id_str]:
            await cancel_warning(member, guild_id_str)
            if member.voice:
                try:
                    await member.edit(mute=False)
                except discord.errors.HTTPException:
                    print(f"Failed to unmute {member.name} in {member.guild.name}")

    # --- Turn camera off in designated channel ---
    if (is_monitored_channel(before.channel)
        and before.self_video
        and not after.self_video
    ):
        await send_warning(member, text_channel, guild_id_str)

async def send_warning(member, text_channel, guild_id_str):
    """Warn user to turn camera on within 2 minutes or be kicked."""
    if member.id in guild_warnings[guild_id_str]:
        # Already warned
        return

    try:
        warning_msg = await text_channel.send(
            f"⚠️ {member.mention}, please turn on your camera within 2 minutes or you will be kicked!"
        )
        guild_warnings[guild_id_str][member.id] = {
            'warning_msg': warning_msg,
            'timer': asyncio.create_task(kick_after_delay(member, warning_msg, guild_id_str))
        }
    except discord.errors.HTTPException as e:
        print(f"Failed to send warning message in {member.guild.name}: {e}")

async def cancel_warning(member, guild_id_str):
    """Cancel a user's active warning in a given guild."""
    if guild_id_str not in guild_warnings or member.id not in guild_warnings[guild_id_str]:
        return

    try:
        guild_warnings[guild_id_str][member.id]['timer'].cancel()
    except Exception:
        pass

    warning_msg = guild_warnings[guild_id_str][member.id].get('warning_msg')
    if warning_msg:
        try:
            await warning_msg.delete()
        except (discord.errors.NotFound, discord.errors.HTTPException):
            pass

    try:
        del guild_warnings[guild_id_str][member.id]
    except KeyError:
        pass

async def kick_after_delay(member, warning_msg, guild_id_str):
    """Wait for 2 minutes. If user still has camera off, kick them from voice."""
    try:
        await asyncio.sleep(120)
        # If user is still in the warnings dict, they haven't turned on camera
        if (guild_id_str in guild_warnings 
            and member.id in guild_warnings[guild_id_str]):
            voice_channels = config['guilds'][guild_id_str].get('voice_channels', [])
            
            for channel_id in voice_channels:
                channel = bot.get_channel(channel_id)
                if member.voice and member.voice.channel == channel:
                    try:
                        await member.move_to(None)  # Kick from channel
                    except discord.errors.HTTPException:
                        print(f"Failed to kick {member.name} in {member.guild.name}")

            # Remove warning message
            try:
                await warning_msg.delete()
            except (discord.errors.NotFound, discord.errors.HTTPException):
                pass

            # Clean up warnings dict
            try:
                del guild_warnings[guild_id_str][member.id]
            except KeyError:
                pass
    except Exception as e:
        print(f"Error in kick_after_delay for {member.guild.name}: {e}")

# ------------------------- Admin Commands -------------------------

@bot.command()
@commands.has_permissions(administrator=True)
async def addvoicechannel(ctx, channel: discord.VoiceChannel):
    """Add a voice channel to monitor in this server."""
    guild_id_str = str(ctx.guild.id)

    if guild_id_str not in config['guilds']:
        config['guilds'][guild_id_str] = {'voice_channels': []}
    
    if 'voice_channels' not in config['guilds'][guild_id_str]:
        config['guilds'][guild_id_str]['voice_channels'] = []

    voice_channels = config['guilds'][guild_id_str]['voice_channels']
    
    if channel.id in voice_channels:
        await ctx.send(f"{channel.mention} is already being monitored!")
        return

    voice_channels.append(channel.id)
    save_config()
    await ctx.send(f"Added {channel.mention} to monitored voice channels!")

@bot.command()
@commands.has_permissions(administrator=True)
async def removevoicechannel(ctx, channel: discord.VoiceChannel):
    """Remove a voice channel from monitoring in this server."""
    guild_id_str = str(ctx.guild.id)
    
    if (guild_id_str not in config['guilds'] or 
        'voice_channels' not in config['guilds'][guild_id_str] or
        channel.id not in config['guilds'][guild_id_str]['voice_channels']):
        await ctx.send(f"{channel.mention} is not being monitored!")
        return

    config['guilds'][guild_id_str]['voice_channels'].remove(channel.id)
    save_config()
    await ctx.send(f"Removed {channel.mention} from monitored voice channels!")

@bot.command()
@commands.has_permissions(administrator=True)
async def listchannels(ctx):
    """List all monitored channels in this server."""
    guild_id_str = str(ctx.guild.id)
    
    if guild_id_str not in config['guilds']:
        await ctx.send("No channels are configured for this server!")
        return

    guild_config = config['guilds'][guild_id_str]
    voice_channels = guild_config.get('voice_channels', [])
    text_channel_id = guild_config.get('text_channel_id')

    response = ["**Monitored Channels in this Server:**\n"]
    
    if voice_channels:
        response.append("**Voice Channels:**")
        for channel_id in voice_channels:
            channel = ctx.guild.get_channel(channel_id)
            response.append(f"• {channel.mention if channel else 'Unknown Channel'}")
    else:
        response.append("No voice channels configured.")

    if text_channel_id:
        text_channel = ctx.guild.get_channel(text_channel_id)
        response.append(f"\n**Warning Channel:**\n• {text_channel.mention if text_channel else 'Unknown Channel'}")
    else:
        response.append("\nNo warning channel configured.")

    await ctx.send('\n'.join(response))

@bot.command()
@commands.has_permissions(administrator=True)
async def settextchannel(ctx, channel: discord.TextChannel):
    """Set the designated text channel for this server."""
    guild_id_str = str(ctx.guild.id)

    if guild_id_str not in config['guilds']:
        config['guilds'][guild_id_str] = {}

    config['guilds'][guild_id_str]['text_channel_id'] = channel.id
    save_config()
    await ctx.send(f"Text channel set to {channel.mention}")

def save_config():
    """Save the current configuration to config.json."""
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)

@addvoicechannel.error
@removevoicechannel.error
@settextchannel.error
async def channel_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need administrator permissions to use this command!")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Please mention a valid channel!")
    else:
        await ctx.send(f"An error occurred: {str(error)}")

# ------------------------- Help Command -------------------------
@bot.command()
async def dochelp(ctx):
    """Provides information on how the bot works and lists admin commands for setup."""
    help_text = (
        "**__Bot Overview__**\n"
        "• I automatically mute anyone who joins a monitored voice channel with their camera off.\n"
        "• I send them a warning in the configured text channel.\n"
        "• If they don't turn on the camera within 2 minutes, I kick them from voice.\n\n"
        
        "**__Admin Commands__**\n"
        "1. **!addvoicechannel voice-channel**\n"
        "   - Add a voice channel to monitor.\n"
        "2. **!removevoicechannel voice-channel**\n"
        "   - Remove a voice channel from monitoring.\n"
        "3. **!settextchannel #text-channel**\n"
        "   - Set the text channel for warnings.\n"
        "4. **!listchannels**\n"
        "   - Show all monitored channels.\n"
    )
    await ctx.send(help_text)

bot.run(config['token'])
