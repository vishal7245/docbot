import discord
from discord.ext import commands
import asyncio
import json

# Load configuration
with open('config.json', 'r') as f:
    config = json.load(f)

intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Tracks users currently under a "camera off" warning
warned_users = {}  # { user_id: {"warning_msg": <Message>, "timer": <Task>} }

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    await connect_to_channel()

async def connect_to_channel():
    """Connect the bot to the configured voice channel."""
    voice_channel = bot.get_channel(config['voice_channel_id'])
    if voice_channel:
        try:
            await voice_channel.connect()
            print(f'Connected to voice channel: {voice_channel.name}')
        except Exception as e:
            print(f'Error connecting to voice channel: {e}')

@bot.event
async def on_voice_state_update(member, before, after):
    """
    Triggered whenever someone in the server changes voice state:
     - Joins/leaves a voice channel
     - Mutes/unmutes
     - Turns camera on/off, etc.
    """
    # Ignore bot accounts
    if member.bot:
        return

    voice_channel = bot.get_channel(config['voice_channel_id'])
    text_channel = bot.get_channel(config['text_channel_id'])

    # --- 1) If user joined/was moved into the designated voice channel ---
    if after.channel == voice_channel:
        # Mute them if they are not already muted
        if member.voice and not after.mute:
            try:
                await member.edit(mute=True)
            except discord.errors.HTTPException:
                print(f"Failed to mute {member.name}")

        # If they do NOT have their camera on, warn them
        if not after.self_video:
            await send_warning(member, text_channel)

    # --- 2) If user left/was moved out of the designated voice channel ---
    elif before.channel == voice_channel and after.channel != voice_channel:
        await cancel_warning(member)

    # --- 3) If user turns camera on while in the designated channel ---
    if (before.channel == voice_channel
        and not before.self_video
        and after.self_video):
        # Cancel any active warning and unmute if they are still connected
        if member.id in warned_users:
            await cancel_warning(member)
            if member.voice:  # Still in a channel
                try:
                    await member.edit(mute=False)
                except discord.errors.HTTPException:
                    print(f"Failed to unmute {member.name}")

    # --- 4) If user turns camera off while in the designated channel ---
    if (before.channel == voice_channel
        and before.self_video
        and not after.self_video):
        await send_warning(member, text_channel)

async def send_warning(member, text_channel):
    """
    Send a warning message if the user has camera off.
    Starts a 2-minute timer to kick if they don't turn it on.
    """
    # Don't send another warning if user already has one
    if member.id in warned_users:
        return

    try:
        # Send the warning
        warning_msg = await text_channel.send(
            f"⚠️ {member.mention}, please turn on your camera within 2 minutes or you will be kicked!"
        )

        # Create and store the timer task
        warned_users[member.id] = {
            'warning_msg': warning_msg,
            'timer': asyncio.create_task(kick_after_delay(member, warning_msg))
        }
    except discord.errors.HTTPException as e:
        print(f"Failed to send warning message: {e}")

async def cancel_warning(member):
    """
    Cancel a user's active warning and remove their entry from warned_users.
    Also deletes the warning message if it still exists.
    """
    if member.id not in warned_users:
        return

    # Cancel the timer
    try:
        warned_users[member.id]['timer'].cancel()
    except Exception:
        pass

    # Try to delete the warning message
    warning_msg = warned_users[member.id].get('warning_msg')
    if warning_msg:
        try:
            await warning_msg.delete()
        except (discord.errors.NotFound, discord.errors.HTTPException):
            pass

    # Remove from dict
    try:
        del warned_users[member.id]
    except KeyError:
        pass

async def kick_after_delay(member, warning_msg):
    """
    Waits 2 minutes and then kicks user from the voice channel if they still
    haven't turned on their camera.
    """
    try:
        await asyncio.sleep(120)  # 2 minutes
        if member.id in warned_users:
            voice_channel = bot.get_channel(config['voice_channel_id'])
            # Only kick if the user is still in the designated voice channel
            if member.voice and member.voice.channel == voice_channel:
                try:
                    await member.move_to(None)  # Kick from channel
                except discord.errors.HTTPException:
                    print(f"Failed to kick {member.name}")

            # Clean up message
            try:
                await warning_msg.delete()
            except (discord.errors.NotFound, discord.errors.HTTPException):
                pass

            # Remove from dict if still there
            try:
                del warned_users[member.id]
            except KeyError:
                pass
    except Exception as e:
        print(f"Error in kick_after_delay: {e}")

# ------------------------- Admin Commands -------------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def setvoicechannel(ctx, channel: discord.VoiceChannel):
    """Set the designated voice channel. Admin only."""
    config['voice_channel_id'] = channel.id
    save_config()
    await ctx.send(f"Voice channel set to {channel.mention}")
    
    # Reconnect to new voice channel
    if ctx.guild.voice_client:
        await ctx.guild.voice_client.disconnect()
    await connect_to_channel()

@bot.command()
@commands.has_permissions(administrator=True)
async def settextchannel(ctx, channel: discord.TextChannel):
    """Set the designated text channel. Admin only."""
    config['text_channel_id'] = channel.id
    save_config()
    await ctx.send(f"Text channel set to {channel.mention}")

def save_config():
    """Save the current configuration to config.json."""
    with open('config.json', 'w') as f:
        json.dump(config, f, indent=4)

@setvoicechannel.error
@settextchannel.error
async def channel_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need administrator permissions to use this command!")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Please mention a valid channel!")
    else:
        await ctx.send(f"An error occurred: {str(error)}")

bot.run(config['token'])
