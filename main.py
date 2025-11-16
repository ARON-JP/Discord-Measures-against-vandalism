import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import asyncio
from datetime import datetime, timedelta

# 設定ファイル
CONFIG_FILE = "config.json"
BAN_LIST_FILE = "ban_list.json"

# デフォルトのリスト構造
DEFAULT_BAN_LIST = {
    "user_ids": [],
    "texts": []
}

# 設定ファイルが存在しない場合は作成
if not os.path.exists(BAN_LIST_FILE):
    with open(BAN_LIST_FILE, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_BAN_LIST, f, ensure_ascii=False, indent=2)

# 設定ファイルの読み込み
if not os.path.exists(CONFIG_FILE):
    print("config.jsonが見つかりません。以下の形式で作成してください：")
    print('{"token": "YOUR_BOT_TOKEN"}')
    exit(1)

with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    config = json.load(f)

# 設定のデフォルト値
log_channel_id = config.get("log_channel_id")
danger_role_id = config.get("danger_role_id")
admin_role_ids = config.get("admin_role_ids", [])
default_punishment = config.get("default_punishment", "ban")
timeout_duration_minutes = config.get("timeout_duration_minutes", 60)

# 処理済みユーザーIDを記録（ログの重複送信を防ぐ）
processed_users = set()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)


def load_ban_list():
    """バンリストを読み込む"""
    try:
        with open(BAN_LIST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return DEFAULT_BAN_LIST.copy()


def save_ban_list(ban_list):
    """バンリストを保存する"""
    with open(BAN_LIST_FILE, "w", encoding="utf-8") as f:
        json.dump(ban_list, f, ensure_ascii=False, indent=2)


def save_config():
    """設定を保存する"""
    config["log_channel_id"] = log_channel_id
    config["danger_role_id"] = danger_role_id
    config["admin_role_ids"] = admin_role_ids
    config["default_punishment"] = default_punishment
    config["timeout_duration_minutes"] = timeout_duration_minutes
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


async def get_log_channel(guild):
    """ログチャンネルを取得"""
    global log_channel_id
    if log_channel_id:
        try:
            channel = guild.get_channel(log_channel_id)
            if channel:
                return channel
        except:
            pass
    return None


async def assign_danger_role(member):
    """危険な人にロールを付与（ロール付与できない問題を解決）"""
    global danger_role_id
    if not danger_role_id:
        return False
    
    try:
        # メンバーがサーバーに存在するか確認
        if not isinstance(member, discord.Member):
            print(f"[{datetime.now()}] メンバーオブジェクトが無効です")
            return False
        
        # ロールを取得
        role = member.guild.get_role(danger_role_id)
        if not role:
            print(f"[{datetime.now()}] ロールID {danger_role_id} が見つかりません")
            return False
        
        # 既にロールを持っているか確認
        if role in member.roles:
            return True  # 既に付与されている
        
        # BOTのロール位置を確認
        bot_member = member.guild.get_member(bot.user.id)
        if bot_member:
            bot_top_role = bot_member.top_role
            if bot_top_role.position <= role.position:
                print(f"[{datetime.now()}] 警告: BOTのロール位置が対象ロールより下です。")
                print(f"  BOTのロール位置: {bot_top_role.position}, 対象ロール位置: {role.position}")
                print(f"  ロールを付与するには、BOTのロールを対象ロールより上に配置してください。")
                # それでも試行（権限があれば成功する可能性がある）
        
        # ロールを付与
        await member.add_roles(role, reason="荒らし対策：危険ユーザーとして検知")
        print(f"[{datetime.now()}] ユーザー {member.name} (ID: {member.id}) にロール {role.name} を付与しました")
        return True
        
    except discord.errors.Forbidden as e:
        print(f"[{datetime.now()}] ロール付与の権限がありません")
        print(f"  エラー詳細: {e}")
        print(f"  解決方法:")
        print(f"  1. BOTに「ロールの管理」権限があるか確認")
        print(f"  2. BOTのロールを対象ロールより上に配置")
        print(f"  3. サーバー設定でBOTの権限を確認")
        return False
    except discord.errors.HTTPException as e:
        print(f"[{datetime.now()}] ロール付与でHTTPエラーが発生: {e}")
        return False
    except Exception as e:
        print(f"[{datetime.now()}] ロール付与エラー: {type(e).__name__}: {e}")
        return False


async def send_log_once(guild, user, reason, action_type="検知", message_content=None):
    """ログチャンネルにログを送信（一回のみ）"""
    # 重複チェック用のキー
    log_key = f"{guild.id}_{user.id}_{action_type}"
    
    # 既に処理済みの場合はスキップ
    if log_key in processed_users:
        return None
    
    log_channel = await get_log_channel(guild)
    if not log_channel:
        return None

    try:
        embed = discord.Embed(
            title=f"⚠️ {action_type}",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="ユーザー", value=f"{user.mention} ({user.name})", inline=False)
        embed.add_field(name="ユーザーID", value=str(user.id), inline=False)
        embed.add_field(name="理由", value=reason, inline=False)
        if message_content:
            embed.add_field(name="メッセージ内容", value=message_content[:1000], inline=False)
        
        embed.set_footer(text=f"アクション: {action_type}")
        
        await log_channel.send(embed=embed)
        
        # 処理済みとして記録
        processed_users.add(log_key)
        
        # メモリリークを防ぐため、一定数以上になったら古いものを削除
        if len(processed_users) > 1000:
            # 最新の500件のみ保持
            processed_users.clear()
        
        return True
    except Exception as e:
        print(f"[{datetime.now()}] ログ送信エラー: {e}")
        return None


async def is_admin(member):
    """ユーザーが管理者かどうかをチェック"""
    if not member:
        return False
    
    # 管理者権限を持っているかチェック
    if member.guild_permissions.administrator:
        return True
    
    # 管理者ロールを持っているかチェック
    global admin_role_ids
    if admin_role_ids:
        member_role_ids = [role.id for role in member.roles]
        for admin_role_id in admin_role_ids:
            if admin_role_id in member_role_ids:
                return True
    
    return False


async def ban_user(guild, user_id, reason="荒らし対策"):
    """ユーザーをバンする"""
    try:
        user = await bot.fetch_user(user_id)
        await guild.ban(user, reason=reason, delete_message_days=0)
        print(f"[{datetime.now()}] ユーザー {user.name} (ID: {user_id}) をバンしました")
        return True
    except discord.errors.NotFound:
        print(f"[{datetime.now()}] ユーザーID {user_id} が見つかりません")
        return False
    except discord.errors.Forbidden:
        print(f"[{datetime.now()}] ユーザーID {user_id} をバンする権限がありません")
        return False
    except Exception as e:
        print(f"[{datetime.now()}] バンエラー: {e}")
        return False


async def kick_user(guild, user_id, reason="荒らし対策"):
    """ユーザーをキックする"""
    try:
        member = guild.get_member(user_id)
        if member:
            await member.kick(reason=reason)
            print(f"[{datetime.now()}] ユーザー {member.name} (ID: {user_id}) をキックしました")
            return True
        else:
            # メンバーがサーバーに存在しない場合
            user = await bot.fetch_user(user_id)
            print(f"[{datetime.now()}] ユーザー {user.name} (ID: {user_id}) はサーバーに存在しません")
            return False
    except discord.errors.Forbidden:
        print(f"[{datetime.now()}] ユーザーID {user_id} をキックする権限がありません")
        return False
    except Exception as e:
        print(f"[{datetime.now()}] キックエラー: {e}")
        return False


async def timeout_user(guild, user_id, duration_minutes, reason="荒らし対策"):
    """ユーザーをタイムアウトする"""
    try:
        member = guild.get_member(user_id)
        if member:
            timeout_until = datetime.utcnow() + timedelta(minutes=duration_minutes)
            await member.timeout(timeout_until, reason=reason)
            print(f"[{datetime.now()}] ユーザー {member.name} (ID: {user_id}) を {duration_minutes}分間タイムアウトしました")
            return True
        else:
            print(f"[{datetime.now()}] ユーザーID {user_id} はサーバーに存在しません")
            return False
    except discord.errors.Forbidden:
        print(f"[{datetime.now()}] ユーザーID {user_id} をタイムアウトする権限がありません")
        return False
    except Exception as e:
        print(f"[{datetime.now()}] タイムアウトエラー: {e}")
        return False


async def unban_user(guild, user_id, reason="バン解除"):
    """ユーザーのバンを解除する"""
    try:
        user = await bot.fetch_user(user_id)
        await guild.unban(user, reason=reason)
        print(f"[{datetime.now()}] ユーザー {user.name} (ID: {user_id}) のバンを解除しました")
        return True
    except discord.errors.NotFound:
        # バンされていない場合もエラーになるが、これは無視
        print(f"[{datetime.now()}] ユーザーID {user_id} はバンされていないか、見つかりません")
        return False
    except discord.errors.Forbidden:
        print(f"[{datetime.now()}] ユーザーID {user_id} のバンを解除する権限がありません")
        return False
    except Exception as e:
        print(f"[{datetime.now()}] バン解除エラー: {e}")
        return False


async def check_user_in_list(user_id):
    """ユーザーIDがリストに含まれているかチェック"""
    ban_list = load_ban_list()
    return str(user_id) in [str(uid) for uid in ban_list["user_ids"]]


async def check_text_in_message(message_content):
    """メッセージに禁止文字列が含まれているかチェック"""
    ban_list = load_ban_list()
    message_lower = message_content.lower()
    for text in ban_list["texts"]:
        if text.lower() in message_lower:
            return True, text
    return False, None


async def apply_punishment(guild, user_id, reason="荒らし対策"):
    """設定された処罰方法を適用する"""
    global default_punishment, timeout_duration_minutes
    
    if default_punishment == "ban":
        return await ban_user(guild, user_id, reason)
    elif default_punishment == "kick":
        return await kick_user(guild, user_id, reason)
    elif default_punishment == "timeout":
        return await timeout_user(guild, user_id, timeout_duration_minutes, reason)
    else:
        # デフォルトはバン
        return await ban_user(guild, user_id, reason)


@bot.event
async def on_ready():
    print(f"[{datetime.now()}] {bot.user} としてログインしました")
    print(f"[{datetime.now()}] 接続中のサーバー数: {len(bot.guilds)}")
    
    # インテントの状態を確認
    print(f"[{datetime.now()}] インテント状態:")
    print(f"  - message_content: {bot.intents.message_content}")
    print(f"  - members: {bot.intents.members}")
    print(f"  - guilds: {bot.intents.guilds}")
    
    # スラッシュコマンドを同期
    try:
        synced = await bot.tree.sync()
        print(f"[{datetime.now()}] {len(synced)} 個のスラッシュコマンドを同期しました")
    except Exception as e:
        print(f"[{datetime.now()}] コマンド同期エラー: {e}")
    
    # 定期チェックタスクを開始
    if not periodic_check.is_running():
        periodic_check.start()
        print(f"[{datetime.now()}] 定期チェックタスクを開始しました（5秒間隔）")


@tasks.loop(seconds=5)
async def periodic_check():
    """5秒ごとにリストをチェック"""
    ban_list = load_ban_list()
    for guild in bot.guilds:
        try:
            # サーバーの全メンバーをチェック
            async for member in guild.fetch_members(limit=None):
                # 管理者は除外
                if await is_admin(member):
                    continue
                
                user_id = str(member.id)
                if user_id in [str(uid) for uid in ban_list["user_ids"]]:
                    # ロールを付与
                    await assign_danger_role(member)
                    # ログを送信（一回のみ）
                    await send_log_once(guild, member, "リストに記載されているユーザーID", "定期チェック検知")
                    # 設定された処罰を適用
                    await apply_punishment(guild, member.id, "リストに記載されているユーザーID")
                    await asyncio.sleep(0.5)  # レート制限対策
        except discord.errors.Forbidden:
            continue
        except Exception as e:
            print(f"[{datetime.now()}] 定期チェックエラー (Guild: {guild.name}): {e}")


@bot.event
async def on_member_join(member):
    """ユーザーがサーバーに参加したとき"""
    # 管理者は除外
    if await is_admin(member):
        return
    
    if await check_user_in_list(member.id):
        # ロールを付与
        await assign_danger_role(member)
        # ログを送信（一回のみ）
        await send_log_once(member.guild, member, "リストに記載されているユーザーID", "参加時検知")
        # 設定された処罰を適用
        await apply_punishment(member.guild, member.id, "リストに記載されているユーザーID（参加時検知）")


@bot.event
async def on_message(message):
    """メッセージが送信されたとき"""
    # BOT自身のメッセージは無視
    if message.author.bot:
        await bot.process_commands(message)
        return

    # 管理者は除外（誤検知を防ぐ）
    member = message.guild.get_member(message.author.id)
    if member and await is_admin(member):
        await bot.process_commands(message)
        return

    # メンションされた場合もチェック
    if bot.user in message.mentions:
        if await check_user_in_list(message.author.id):
            # メンバーオブジェクトを取得
            if member:
                # ロールを付与
                await assign_danger_role(member)
            # ログを送信（一回のみ）
            await send_log_once(message.guild, message.author, "リストに記載されているユーザーID", "メンション時検知", message.content)
            # 設定された処罰を適用
            await apply_punishment(message.guild, message.author.id, "リストに記載されているユーザーID（メンション時検知）")
            try:
                await message.delete()
            except:
                pass
            await bot.process_commands(message)
            return

    # メッセージ内容をチェック
    detected, detected_text = await check_text_in_message(message.content)
    if detected:
        # メッセージを削除
        try:
            await message.delete()
        except:
            pass

        # メンバーオブジェクトを取得
        if member:
            # ロールを付与
            await assign_danger_role(member)
        
        # ログを送信（一回のみ）
        await send_log_once(message.guild, message.author, f"禁止文字列を検知: {detected_text}", "禁止文字列検知", message.content)
        
        # 設定された処罰を適用
        await apply_punishment(message.guild, message.author.id, f"禁止文字列を検知: {detected_text}")

        # ユーザーIDをリストに追加
        ban_list = load_ban_list()
        user_id_str = str(message.author.id)
        if user_id_str not in [str(uid) for uid in ban_list["user_ids"]]:
            ban_list["user_ids"].append(user_id_str)
            save_ban_list(ban_list)
            print(f"[{datetime.now()}] ユーザーID {user_id_str} をリストに追加しました")

    await bot.process_commands(message)


@bot.tree.command(name="add", description="リストにテキストまたはユーザーIDを追加")
async def add_command(interaction: discord.Interaction, list_type: str, value: str):
    """リストに追加するコマンド"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    ban_list = load_ban_list()

    if list_type.lower() == "text":
        if value not in ban_list["texts"]:
            ban_list["texts"].append(value)
            save_ban_list(ban_list)
            await interaction.response.send_message(f"テキスト `{value}` をリストに追加しました。", ephemeral=True)
        else:
            await interaction.response.send_message(f"テキスト `{value}` は既にリストに存在します。", ephemeral=True)

    elif list_type.lower() == "user":
        user_id_str = str(value)
        if user_id_str not in [str(uid) for uid in ban_list["user_ids"]]:
            ban_list["user_ids"].append(user_id_str)
            save_ban_list(ban_list)
            await interaction.response.send_message(f"ユーザーID `{user_id_str}` をリストに追加しました。", ephemeral=True)
        else:
            await interaction.response.send_message(f"ユーザーID `{user_id_str}` は既にリストに存在します。", ephemeral=True)

    else:
        await interaction.response.send_message("list_typeは 'text' または 'user' を指定してください。", ephemeral=True)


@bot.tree.command(name="remove", description="リストからテキストまたはユーザーIDを削除")
async def remove_command(interaction: discord.Interaction, list_type: str, value: str):
    """リストから削除するコマンド"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    ban_list = load_ban_list()

    if list_type.lower() == "text":
        if value in ban_list["texts"]:
            ban_list["texts"].remove(value)
            save_ban_list(ban_list)
            await interaction.response.send_message(f"テキスト `{value}` をリストから削除しました。", ephemeral=True)
        else:
            await interaction.response.send_message(f"テキスト `{value}` はリストに存在しません。", ephemeral=True)

    elif list_type.lower() == "user":
        user_id_str = str(value)
        user_ids_str = [str(uid) for uid in ban_list["user_ids"]]
        if user_id_str in user_ids_str:
            ban_list["user_ids"] = [uid for uid in ban_list["user_ids"] if str(uid) != user_id_str]
            save_ban_list(ban_list)
            await interaction.response.send_message(f"ユーザーID `{user_id_str}` をリストから削除しました。", ephemeral=True)
        else:
            await interaction.response.send_message(f"ユーザーID `{user_id_str}` はリストに存在しません。", ephemeral=True)

    else:
        await interaction.response.send_message("list_typeは 'text' または 'user' を指定してください。", ephemeral=True)


@bot.tree.command(name="list", description="現在のリストを表示")
async def list_command(interaction: discord.Interaction):
    """リストを表示するコマンド"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    ban_list = load_ban_list()
    
    user_ids_text = "\n".join(ban_list["user_ids"]) if ban_list["user_ids"] else "なし"
    texts_text = "\n".join(ban_list["texts"]) if ban_list["texts"] else "なし"

    embed = discord.Embed(title="バンリスト", color=discord.Color.red())
    embed.add_field(name="ユーザーID", value=f"```\n{user_ids_text}\n```", inline=False)
    embed.add_field(name="禁止テキスト", value=f"```\n{texts_text}\n```", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="setlog", description="ログチャンネルを設定")
async def setlog_command(interaction: discord.Interaction, channel: discord.TextChannel):
    """ログチャンネルを設定するコマンド"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    global log_channel_id
    log_channel_id = channel.id
    save_config()
    await interaction.response.send_message(f"ログチャンネルを {channel.mention} に設定しました。", ephemeral=True)


@bot.tree.command(name="setrole", description="危険ユーザーに付与するロールを設定")
async def setrole_command(interaction: discord.Interaction, role: discord.Role):
    """危険ロールを設定するコマンド"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    global danger_role_id
    danger_role_id = role.id
    save_config()
    
    # BOTのロール位置を確認
    bot_member = interaction.guild.get_member(bot.user.id)
    if bot_member:
        bot_top_role = bot_member.top_role
        if bot_top_role.position <= role.position:
            await interaction.response.send_message(
                f"危険ユーザーロールを {role.mention} に設定しました。\n"
                f"⚠️ 警告: BOTのロール位置が対象ロールより下です。\n"
                f"ロールを付与するには、BOTのロールを対象ロールより上に配置してください。",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(f"危険ユーザーロールを {role.mention} に設定しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"危険ユーザーロールを {role.mention} に設定しました。", ephemeral=True)


@bot.tree.command(name="clearlog", description="処理済みログの記録をクリア（テスト用）")
async def clearlog_command(interaction: discord.Interaction):
    """処理済みログの記録をクリアするコマンド"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    global processed_users
    count = len(processed_users)
    processed_users.clear()
    await interaction.response.send_message(f"処理済みログの記録をクリアしました（{count}件）。", ephemeral=True)


@bot.tree.command(name="unban", description="ユーザーのバンを解除し、リストから削除")
async def unban_command(interaction: discord.Interaction, user_id: str):
    """ユーザーのバンを解除し、リストから削除するコマンド"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    try:
        user_id_int = int(user_id)
    except ValueError:
        await interaction.response.send_message("無効なユーザーIDです。", ephemeral=True)
        return

    # バンを解除
    unban_result = await unban_user(interaction.guild, user_id_int, f"管理者 {interaction.user.name} による解除")

    # リストから削除
    ban_list = load_ban_list()
    user_id_str = str(user_id_int)
    user_ids_str = [str(uid) for uid in ban_list["user_ids"]]
    
    removed_from_list = False
    if user_id_str in user_ids_str:
        ban_list["user_ids"] = [uid for uid in ban_list["user_ids"] if str(uid) != user_id_str]
        save_ban_list(ban_list)
        removed_from_list = True

    # 結果を返す
    result_messages = []
    if unban_result:
        result_messages.append("✅ バンを解除しました")
    else:
        result_messages.append("⚠️ バン解除に失敗しました（既に解除されているか、バンされていない可能性があります）")
    
    if removed_from_list:
        result_messages.append("✅ リストから削除しました")
    else:
        result_messages.append("⚠️ リストに存在しませんでした")

    embed = discord.Embed(
        title="バン解除結果",
        description="\n".join(result_messages),
        color=discord.Color.green() if (unban_result or removed_from_list) else discord.Color.orange()
    )
    embed.add_field(name="ユーザーID", value=user_id_str, inline=False)
    embed.set_footer(text=f"実行者: {interaction.user.name}")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="setadminrole", description="管理者ロールを設定（複数指定可能）")
async def setadminrole_command(interaction: discord.Interaction, role: discord.Role):
    """管理者ロールを設定するコマンド"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    global admin_role_ids
    if role.id not in admin_role_ids:
        admin_role_ids.append(role.id)
        save_config()
        await interaction.response.send_message(f"管理者ロールに {role.mention} を追加しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"{role.mention} は既に管理者ロールに設定されています。", ephemeral=True)


@bot.tree.command(name="removeadminrole", description="管理者ロールから削除")
async def removeadminrole_command(interaction: discord.Interaction, role: discord.Role):
    """管理者ロールから削除するコマンド"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    global admin_role_ids
    if role.id in admin_role_ids:
        admin_role_ids.remove(role.id)
        save_config()
        await interaction.response.send_message(f"管理者ロールから {role.mention} を削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"{role.mention} は管理者ロールに設定されていません。", ephemeral=True)


@bot.tree.command(name="listadminroles", description="現在の管理者ロール一覧を表示")
async def listadminroles_command(interaction: discord.Interaction):
    """管理者ロール一覧を表示するコマンド"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    global admin_role_ids
    if not admin_role_ids:
        await interaction.response.send_message("管理者ロールが設定されていません。\n注: 管理者権限を持つユーザーは自動的に除外されます。", ephemeral=True)
        return

    roles_text = []
    for role_id in admin_role_ids:
        role = interaction.guild.get_role(role_id)
        if role:
            roles_text.append(f"{role.mention} (ID: {role_id})")
        else:
            roles_text.append(f"不明なロール (ID: {role_id})")

    embed = discord.Embed(title="管理者ロール一覧", color=discord.Color.blue())
    embed.add_field(name="ロール", value="\n".join(roles_text) if roles_text else "なし", inline=False)
    embed.set_footer(text="注: 管理者権限を持つユーザーも自動的に除外されます")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="punish", description="検知時に自動適用される全体の処罰方法を設定")
@app_commands.describe(
    punishment_type="検知時に全ユーザーに自動適用する処罰の種類（全体設定）",
    timeout_minutes="タイムアウトの時間（分）。timeoutを選択した場合のみ必要"
)
@app_commands.choices(punishment_type=[
    app_commands.Choice(name="バン", value="ban"),
    app_commands.Choice(name="キック", value="kick"),
    app_commands.Choice(name="タイムアウト", value="timeout")
])
async def punish_command(
    interaction: discord.Interaction,
    punishment_type: app_commands.Choice[str],
    timeout_minutes: int = None
):
    """検知時に自動適用される全体の処罰方法を設定するコマンド"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    global default_punishment, timeout_duration_minutes
    punishment_type_value = punishment_type.value.lower()

    # タイムアウトの場合は時間が必要
    if punishment_type_value == "timeout":
        if timeout_minutes is None:
            # 既存の設定値を使用
            timeout_minutes = timeout_duration_minutes
        elif timeout_minutes < 1 or timeout_minutes > 40320:  # 最大28日
            await interaction.response.send_message(
                "タイムアウト時間は1分から40320分（28日）の間で指定してください。",
                ephemeral=True
            )
            return
        else:
            # 新しいタイムアウト時間を設定
            timeout_duration_minutes = timeout_minutes

    # 全体の処罰方法を設定
    default_punishment = punishment_type_value
    save_config()

    # 結果を返す
    punishment_names = {
        "ban": "バン",
        "kick": "キック",
        "timeout": f"タイムアウト ({timeout_duration_minutes}分)"
    }
    
    punishment_name = punishment_names.get(punishment_type_value, punishment_type_value)
    
    embed = discord.Embed(
        title="✅ 全体の処罰設定を更新しました",
        description=f"今後検知されるすべてのユーザーに自動適用される処罰: **{punishment_name}**\n\nこの設定は、リストに記載されているユーザーIDや禁止文字列を検知した際に、すべてのユーザーに適用されます。",
        color=discord.Color.green()
    )
    if punishment_type_value == "timeout":
        embed.add_field(name="タイムアウト時間", value=f"{timeout_duration_minutes}分", inline=False)
    embed.set_footer(text=f"設定者: {interaction.user.name}")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="punishstatus", description="現在の全体処罰設定を確認")
async def punishstatus_command(interaction: discord.Interaction):
    """現在の全体処罰設定を確認するコマンド"""
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("このコマンドは管理者のみ使用できます。", ephemeral=True)
        return

    global default_punishment, timeout_duration_minutes
    
    punishment_names = {
        "ban": "バン",
        "kick": "キック",
        "timeout": f"タイムアウト ({timeout_duration_minutes}分)"
    }
    
    punishment_name = punishment_names.get(default_punishment, default_punishment)
    
    embed = discord.Embed(
        title="現在の全体処罰設定",
        description=f"検知時に自動適用される処罰: **{punishment_name}**",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="説明",
        value="この設定は、リストに記載されているユーザーIDや禁止文字列を検知した際に、すべてのユーザーに自動適用されます。",
        inline=False
    )
    if default_punishment == "timeout":
        embed.add_field(name="タイムアウト時間", value=f"{timeout_duration_minutes}分", inline=False)
    embed.set_footer(text="設定を変更するには /punish コマンドを使用してください")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    token = config.get("token")
    if not token:
        print("config.jsonにトークンが設定されていません。")
        exit(1)
    
    try:
        bot.run(token)
    except discord.errors.PrivilegedIntentsRequired as e:
        print("\n" + "="*60)
        print("【エラー】特権インテントが有効化されていません")
        print("="*60)
        print("\nこのBOTを動作させるには、Discord Developer Portalで")
        print("以下の特権インテントを有効化する必要があります：\n")
        print("1. MESSAGE CONTENT INTENT（メッセージ内容の読み取り）")
        print("2. SERVER MEMBERS INTENT（メンバー情報の取得）\n")
        print("設定手順：")
        print("1. https://discord.com/developers/applications/ にアクセス")
        print("2. あなたのBOTアプリケーションを選択")
        print("3. 「Bot」タブを開く")
        print("4. 「Privileged Gateway Intents」セクションで以下を有効化：")
        print("   ✓ MESSAGE CONTENT INTENT")
        print("   ✓ SERVER MEMBERS INTENT")
        print("5. 変更を保存")
        print("6. BOTを再起動\n")
        print("="*60)
        exit(1)
    except discord.errors.LoginFailure:
        print("\n" + "="*60)
        print("【エラー】BOTトークンが無効です")
        print("="*60)
        print("\nconfig.jsonのトークンが正しいか確認してください。")
        print("トークンはDiscord Developer Portalの「Bot」タブで確認できます。")
        print("="*60)
        exit(1)
    except Exception as e:
        print(f"\n【予期しないエラーが発生しました】\n{type(e).__name__}: {e}")
        exit(1)

