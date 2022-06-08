# Shop was made by Redjumpman for Red Bot.

# Standard Library
import asyncio
import csv
import logging
import random
import textwrap
import uuid
import sys
import datetime
import random

from bisect import bisect
from copy import deepcopy
from itertools import zip_longest
from typing import Literal

# Shop
from .menu import ShopMenu
from .inventory import Inventory
from .checks import Checks
from .giftcard import giftCardIndex

# Discord.py
import discord

# Red
from redbot.core import Config, bank, commands
from redbot.core.utils import AsyncIter
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.data_manager import bundled_data_path, cog_data_path
from redbot.core.errors import BalanceTooHigh

#Adventure
from adventure.adventure import Adventure
from adventure.charsheet import Character, Item
from adventure.economy import EconomyCommands

from disputils import BotEmbedPaginator, BotConfirmation, BotMultipleChoice
from collections import namedtuple

log = logging.getLogger("red.shop")

__version__ = "3.1.13"
__author__ = "Ataraxy"

prefix = {
    'distributable': '[D]',
    'redeemable': '[R]',
    }
itemCashType = {
    'nitro': 'Discord Nitro',
    'giftcard': 'Cash Item',
    'goldpass': 'Gold Pass',
}

def global_permissions():
    async def pred(ctx: commands.Context):
        is_owner = await ctx.bot.is_owner(ctx.author)
        if is_owner:
            return True
        if not await Shop().shop_is_global() and ctx.guild:
            permissions = ctx.channel.permissions_for(ctx.author)
            admin_roles = await ctx.bot._config.guild(ctx.guild).admin_role()
            author_roles = [role.id for role in ctx.author.roles]
            admin_role_check = check_if_role_in_roles(admin_roles, author_roles)
            return admin_role_check or (ctx.author == ctx.guild.owner) or permissions.administrator

    return commands.check(pred)

def check_if_role_in_roles(admin_roles, user_roles):
    intersection = list(set(admin_roles).intersection(user_roles))
    if not intersection:
        return False
    return True

async def giftcard_availability(self, ctx):
    regionSelect = list(giftCardIndex.keys())
    regionSelect.sort()
    regionChoice = BotMultipleChoice(ctx,regionSelect,f"{ctx.author.display_name}, which World Region do you reside in?")
    await regionChoice.run()

    if regionChoice.choice == None:
        await regionChoice.quit(f"{ctx.author.mention}, selection has been cancelled.")
        return None, None
    else:
        userRegion = regionChoice.choice
        countrySelect = list(giftCardIndex[str(userRegion)].keys())
        countrySelect.sort()
        countrySelect.append("My Country is not on this list.")
        
        countryChoice = BotMultipleChoice(ctx,countrySelect,f"{ctx.author.display_name}, select your Country from the below list.")
        await regionChoice.quit()
        await countryChoice.run()
        if countryChoice.choice == None:
            await countryChoice.quit(f"{ctx.author.mention}, selection has been cancelled.")
            return None, None
        else:
            await countryChoice.quit()
            userCountry = countryChoice.choice
            if userCountry == "My Country is not on this list.":
                cashList = []
            else:
                cashList = giftCardIndex[str(userRegion)][str(userCountry)]
            cashList.append("Discord Nitro")
            return userCountry, cashList

class Shop(commands.Cog):
    shop_defaults = {
        "Shops": {},
        "Settings": {"Alerts": False, "Alert_Role": "Admin", "Redeem_Role": "", "Distribution_Channel": "", "Closed": False, "Gifting": True, "Sorting": "price",},
        "Pending": {},
    }
    member_defaults = {
        "Inventory": {},
        "Trading": True,
    }
    user_defaults = member_defaults
    global_defaults = deepcopy(shop_defaults)
    global_defaults["Global"] = False

    def __init__(self):
        self.config = Config.get_conf(self, 5074395003, force_registration=True)
        self.config.register_guild(**self.shop_defaults)
        self.config.register_global(**self.global_defaults)
        self.config.register_member(**self.member_defaults)
        self.config.register_user(**self.user_defaults)

    async def red_delete_data_for_user(
        self, *, requester: Literal["discord", "owner", "user", "user_strict"], user_id: int
    ):
        await self.config.user_from_id(user_id).clear()
        all_members = await self.config.all_members()
        async for guild_id, guild_data in AsyncIter(all_members.items(), steps=100):
            if user_id in guild_data:
                await self.config.member_from_ids(guild_id, user_id).clear()      
            
    # -----------------------COMMANDS-------------------------------------

    @commands.command(name="checkcash")
    @commands.max_concurrency(1, commands.BucketType.user)
    async def giftcard_browse(self, ctx):
        """Check Cash Item availability based on Country."""
        timestamp = datetime.datetime.now()
        userCountry, cashList = await giftcard_availability(self, ctx)

        cashListdesc = ""
        num = 0
        for item in cashList:
            num += 1
            cashListdesc += f"> **{num}** {item}\n"

        embed = discord.Embed(title="",
                        description=f"You may redeem your ATC for the below Cash items, based on the information you provided."+
                                    f"\n\u200b\n**Country: {userCountry}**"
                                    f"\n\u200b\n{cashListdesc}"+
                                    f"\n*All redemptions are subject to market availability at the time of redemption. This list is __not__ a guarantee of redemption.*",
                        color=await ctx.embed_color())
        embed.set_author(name=f"{ctx.author.display_name}#{ctx.author.discriminator}",icon_url=ctx.author.avatar_url)
        embed.set_footer(text=f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

        await ctx.send(embed=embed)

    @commands.command()
    @commands.max_concurrency(1, commands.BucketType.user)
    async def inventory(self, ctx):
        """Displays your purchased items."""
        try:
            instance = await self.get_instance(ctx, user=ctx.author)
        except AttributeError:
            return await ctx.send("You can't use this command in DMs when not in global mode.")
        if not await instance.Inventory():
            return await ctx.send("You don't have any items to display.")
        data = await instance.Inventory.all()
        menu = Inventory(ctx, list(data.items()))

        try:
            item = await menu.display()
        except RuntimeError:
            return
        await self.pending_prompt(ctx, instance, data, item)

    @commands.command(name="store", aliases=["buy"])
    @commands.max_concurrency(1, commands.BucketType.user)
    async def buy(self, ctx):
        """Browse the store, and buy items too."""

        try:
            instance = await self.get_instance(ctx, settings=True)
        except AttributeError:
            return await ctx.send("You can't use this command in DMs when not in global mode.")
        if not await instance.Shops():
            return await ctx.send("No shops have been created yet.")
        if await instance.Settings.Closed():
            return await ctx.send("The shop system is currently closed.")

        shops = await instance.Shops.all()
        col = await self.check_availability(ctx, shops)
        if not col:
            return await ctx.send(
                "Either no items have been created, you need a higher role,  "
                "or this command should be used in a server and not DMs."
            )
        
        style = await instance.Settings.Sorting()
        menu = ShopMenu(ctx, shops, sorting=style)
        try:
            shop, item = await menu.display()
        except RuntimeError:
            return

        user_data = await self.get_instance(ctx, user=ctx.author)
        sm = ShopManager(ctx, instance, user_data)
        try:
            await sm.order(shop, item)
        except asyncio.TimeoutError:
            await ctx.send("Request timed out.")
        except ExitProcess:
            await ctx.send("Transaction canceled.")

    async def inv_hook(self, user):
        """Inventory Hook for outside cogs

        Parameters
        ----------
        user : discord.Member or discord.User

        Returns
        -------
        dict
            Returns a dict of the user's inventory or empty if the user
            is not found.
        """
        try:
            instance = await self._inv_hook_instance(user)
        except AttributeError:
            return {}
        else:
            return await instance.Inventory.all()

    async def _inv_hook_instance(self, user):
        if await self.config.Global():
            return self.config.user(user)
        else:
            return self.config.member(user)

    @commands.max_concurrency(1, commands.BucketType.user)
    @commands.command()
    async def redeem(self, ctx):
        """Redeems Cash Items in your inventory."""

        def check_qty(m):
            return (int(m.content) <= item_data['Qty'])

        try:
            instance = await self.get_instance(ctx, user=ctx.author)
        except AttributeError:
            return await ctx.send("You can't use this command in DMs when not in global mode.")
        data = await instance.Inventory.all()
        if data is None:
            return await ctx.send("Your inventory is empty.")

        redeemable_items = []
        select_item = []
        for item_name, item_data in data.items():
            if item_data['Type'] == 'redeemable':
                item_description = f"**{item_name}** (Qty: {item_data['Qty']})"
                redeemable_items.append(item_name)
                select_item.append(item_description)

        if len(redeemable_items) == 0:
            return await ctx.send(f"{ctx.author.mention}, you have no items available for redemption.")

        redeem_choice = BotMultipleChoice(ctx,select_item,"Select an item to redeem.")
        await redeem_choice.run()

        if redeem_choice.choice == None:
            return await redeem_choice.quit(f"{ctx.author.mention}, redemption has been cancelled.")
        else:
            select_index = select_item.index(redeem_choice.choice)
            item_name = redeemable_items[select_index]
            item_data = data[item_name]

            if item_data['Qty'] > 1:
                ask_qty = await ctx.send(f"How many of **{item_name}** would you like to redeem?")
                try:
                    get_qty = await ctx.bot.wait_for("message", timeout=25, check=check_qty)
                except asyncio.TimeoutError:
                    await ask_qty.delete()
                    return await ctx.send(f"{ctx.author.mention}, redemption has been cancelled.")

                qty = int(get_qty.content)
                await ask_qty.delete()
                await get_qty.delete()
            else:
                qty = 1

            timestamp = datetime.datetime.now()
            await redeem_choice.quit()
            if item_data['cashType'] == 'nitro':
                embed = discord.Embed(title="Redemption Request",
                                        description=f"Item: {item_name}"+
                                                    f"\nQuantity: {qty}"+
                                                    f"\n\u3000\nAll redemption requests will be fulfilled within **72 hours**.",
                                        color=await ctx.embed_color())
                embed.set_author(name=f"{ctx.author.display_name}#{ctx.author.discriminator}",icon_url=ctx.author.avatar_url)
                embed.set_footer(text=f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
                await ctx.send(content=ctx.guild.owner.mention, embed=embed)

            if item_data['cashType'] == 'giftcard' or item_data['cashType'] == 'goldpass':
                gc_notice = await ctx.send("Cash Item redemptions are subject to availability. We can offer alternative arrangements but cannot guarantee the usability of such rewards.")
                userCountry, cashList = await giftcard_availability(self, ctx)

                if not userCountry and not cashList:
                    await gc_notice.delete()
                    return
                
                if item_data['cashType'] == 'goldpass':
                    cashListChoose = []
                if "Apple iTunes" in cashList:
                    cashListChoose.append("Apple iTunes")
                if "Google Play" in cashList:
                    cashListChoose.append("Google Play")
                    cashListChoose.append("Discord Nitro Classic")
                else:
                    cashListChoose = cashList
                
                itemChoice = BotMultipleChoice(ctx,cashListChoose,f"{ctx.author.display_name}, select a platform to receive your Gift Card for.")
                await itemChoice.run()
                if itemChoice.choice == None:
                    return await itemChoice.quit(f"{ctx.author.mention}, redemption has been cancelled.")
                else:
                    itemSelect = itemChoice.choice
                    await itemChoice.quit()

                await gc_notice.delete()
                embed = discord.Embed(title="Redemption Request",
                                    description=f"Item: {item_name}"+
                                                f"\nQuantity: {qty}"+
                                                f"\nPlatform: {itemSelect}"+
                                                f"\nCountry: {userCountry}"+
                                                f"\n\u3000\n**Note: Cash Item redemptions are subject to availability.** Any alternative arrangements are not guaranteed.\n*If no alternatives are available, you will receive the value equivalent of Discord Nitro.*"+
                                                f"\n\nAll redemption requests will be fulfilled within **72 hours**.",
                                        color=await ctx.embed_color())
                embed.set_author(name=f"{ctx.author.display_name}#{ctx.author.discriminator}",icon_url=ctx.author.avatar_url)
                embed.set_footer(text=f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
                await redeem_choice.quit()
                await ctx.send(content=ctx.guild.owner.mention, embed=embed)

            sm = ShopManager(ctx, instance=None,user_data=instance)
            return await sm.remove(item=item_name,number=qty)

    # @commands.command()
    # @commands.guild_only()
    # @commands.cooldown(1, 5, commands.BucketType.user)
    # async def trade(self, ctx, user: discord.Member, quantity: int, *, item: str):
    #     """Attempts to trade an item with another user.

    #     Cooldown is a static 60 seconds to prevent abuse.
    #     Cooldown will trigger regardless of the outcome.
    #     """
    #     cancel = ctx.prefix + "cancel"
    #     author_instance = await self.get_instance(ctx, user=ctx.author)
    #     author_inventory = await author_instance.Inventory.all()
    #     user_instance = await self.get_instance(ctx, user=user)
    #     user_inv = await user_instance.Inventory.all()

    #     if not await user_instance.Trading():
    #         return await ctx.send("This user has trading turned off.")

    #     if item not in author_inventory:
    #         return await ctx.send("You don't own that item.")

    #     if 0 < author_inventory[item]["Qty"] < quantity:
    #         return await ctx.send("You don't have that many {}".format(item))

    #     await ctx.send(
    #         "{} has requested a trade with {}.\n"
    #         "They are offering {}x {}.\n Do wish to trade?\n"
    #         "*This trade can be canceled at anytime by typing `{}`.*"
    #         "".format(ctx.author.mention, user.mention, quantity, item, cancel)
    #     )

    #     def check(m):
    #         return (m.author == user and m.content.lower() in ("yes", "no", cancel)) or (
    #             m.author == ctx.author and m.content.lower() == cancel
    #         )

    #     try:
    #         decision = await ctx.bot.wait_for("message", timeout=25, check=check)
    #     except asyncio.TimeoutError:
    #         return await ctx.send("Trade request timed out. Canceled trade.")

    #     if decision.content.lower() in ("no", cancel):
    #         return await ctx.send("Trade canceled.")
    #     await ctx.send("{} What is your counter offer?\n" '*Example: 3 "Healing Potions"*'.format(user.mention))

    #     def predicate(m):
    #         if m.author in (user, ctx.author) and m.content == cancel:
    #             return True
    #         if m.author != user:
    #             return False
    #         try:
    #             q, i = [x.strip() for x in m.content.split('"')[:2] if x]
    #         except ValueError:
    #             return False
    #         else:
    #             if i not in user_inv:
    #                 return False
    #             return 0 < user_inv[i]["Qty"] <= int(q)

    #     try:
    #         offer = await ctx.bot.wait_for("message", timeout=25, check=predicate)
    #     except asyncio.TimeoutError:
    #         return await ctx.send("Trade request timed out. Canceled trade.")
    #     if offer.content.lower() == cancel:
    #         return await ctx.send("Trade canceled.")
    #     qty, item2 = [x.strip() for x in offer.content.split('"')[:2] if x]
    #     await ctx.send(
    #         "{} Do you wish to trade {}x {} for {}'s {}x {}?"
    #         "".format(ctx.author.mention, quantity, item, user.mention, qty, item2)
    #     )

    #     def check2(m):
    #         return (m.author == ctx.author and m.content.lower() in ("yes", "no", cancel)) or (
    #             m.author == user and m.content.lower() == cancel
    #         )

    #     try:
    #         final = await ctx.bot.wait_for("message", timeout=25, check=check2)
    #     except asyncio.TimeoutError:
    #         return await ctx.send("Trade request timed out. Canceled trade.")

    #     if final.content.lower() in ("no", cancel):
    #         return await ctx.send("Trade canceled.")

    #     sm1 = ShopManager(ctx, instance=None, user_data=author_instance)
    #     await sm1.add(item2, user_inv[item2], int(qty))
    #     await sm1.remove(item, number=quantity)

    #     sm2 = ShopManager(ctx, instance=None, user_data=user_instance)
    #     await sm2.add(item, author_inventory[item], quantity)
    #     await sm2.remove(item2, number=int(qty))

    #     await ctx.send("Trade complete.")

    # @commands.command()
    # async def tradetoggle(self, ctx):
    #     """Disables or enables trading with you."""
    #     try:
    #         instance = await self.get_instance(ctx, user=ctx.author)
    #     except AttributeError:
    #         return await ctx.send("You can't use this command in DMs when not in global mode.")
    #     status = await instance.Trading()
    #     await instance.Trading.set(not status)
    #     await ctx.send("Trading with you is now {}.".format("disabled" if status else "enabled"))

    #@commands.command()
    #@commands.guild_only()
    #async def gift(self, ctx, user: discord.Member, quantity: int, *, item):
        """Gift another user a set number of one of your items.

        The item must be in your inventory and have enough to cover the quantity.

        Examples
        --------
        [p]shop gift Redjumpman 3 Healing Potion
        [p]shop give @Navi 1 Demon Sword
        """
    #    if quantity < 1:
    #        return await ctx.send(":facepalm: How would that work genius?")
    #    if user == ctx.author:
    #        return await ctx.send("Really? Maybe you should find some friends.")
    #    settings = await self.get_instance(ctx, settings=True)
    #    if not await settings.Settings.Gifting():
    #        return await ctx.send("Gifting is turned off.")
    #    author_instance = await self.get_instance(ctx, user=ctx.author)
    #    author_inv = await author_instance.Inventory.all()
    #    if item not in author_inv:
    #        return await ctx.send(f"You don't own any `{item}`.")
    #    if author_inv[item]["Qty"] < quantity:
    #        return await ctx.send(f"You don't have that many `{item}` to give.")

    #    sm1 = ShopManager(ctx, instance=None, user_data=author_instance)
    #    await sm1.remove(item, number=quantity)

    #    user_instance = await self.get_instance(ctx, user=user)
    #    sm2 = ShopManager(ctx, instance=None, user_data=user_instance)
    #    await sm2.add(item, author_inv[item], quantity)

    #    await ctx.send(f"{ctx.author.mention} gifted {user.mention} {quantity}x {item}.")


    @commands.group(autohelp=True)
    async def shopadmin(self, ctx):
        """Shop Admin group command"""
        pass

    @shopadmin.command()
    async def version(self, ctx):
        """Shows the current Shop version."""
        await ctx.send("Shop is running version {}.".format(__version__))

    @shopadmin.command()
    @commands.is_owner()
    async def wipe(self, ctx):
        """Wipes all shop cog data."""
        await ctx.send(
            "You are about to delete all shop and user data from the bot. Are you sure this is what you wish to do?"
        )

        try:
            choice = await ctx.bot.wait_for("message", timeout=25.0, check=Checks(ctx).confirm)
        except asyncio.TimeoutError:
            return await ctx.send("No Response. Action canceled.")

        if choice.content.lower() == "yes":
            await self.config.clear_all()
            msg = "{0.name} ({0.id}) wiped all shop data.".format(ctx.author)
            log.info(msg)
            await ctx.send(msg)
        else:
            return await ctx.send("Wipe canceled.")

    # @shopadmin.command()
    # @global_permissions()
    # @commands.guild_only()
    # @commands.max_concurrency(1, commands.BucketType.user)
    # async def pending(self, ctx):
    #     """Displays the pending menu."""
    #     instance = await self.get_instance(ctx, settings=True)
    #     if not await instance.Pending():
    #         return await ctx.send("There are not any pending items.")
    #     data = await instance.Pending.all()
    #     menu = ShopMenu(ctx, data, mode=1, sorting="name")

    #     try:
    #         user, item, = await menu.display()
    #     except RuntimeError:
    #         return

    #     try:
    #         await self.clear_single_pending(ctx, instance, data, item, user)
    #     except asyncio.TimeoutError:
    #         await ctx.send("Request timed out.")

    @shopadmin.command()
    @global_permissions()
    @commands.guild_only()
    async def give(self, ctx, user: discord.Member, quantity: int, *shopitem):
        """Administratively gives a user an item.

        Shopitem argument must be a \"Shop Name\" \"Item Name\" format.

        The item must be in the shop in order for this item to be given.
        Only basic and role items can be given.
        Giving a user an item does not affect the stock in the shop.

        Examples
        --------
        [p]shop give Redjumpman 1 "Holy Temple" "Healing Potion"
        [p]shop give Redjumpman 1 Junkyard Scrap
        """
        if quantity < 1:
            return await ctx.send(":facepalm: You can't do that.")

        if shopitem is None:
            return await ctx.send_help()

        try:
            shop, item = shopitem
        except ValueError:
            return await ctx.send('Must be a `"Shop Name" "Item Name"` format.')

        instance = await self.get_instance(ctx, settings=True)
        shops = await instance.Shops.all()
        if shop not in shops:
            return await ctx.send("Invalid shop name.")
        elif item not in shops[shop]["Items"]:
            return await ctx.send("That item in not in the {} shop.".format(shop))
        elif shops[shop]["Items"][item]["Type"] not in ("basic", "role", "distributable","redeemable"):
            return await ctx.send("You can only give basic or role type items.")
        else:
            data = deepcopy(shops[shop]["Items"][item])
            user_instance = await self.get_instance(ctx, user=user)
            sm = ShopManager(ctx, None, user_instance)
            await sm.add(item, data, quantity)
            await ctx.send("{} just gave {} a {}.".format(ctx.author.mention, user.mention, item))

    @shopadmin.command()
    @global_permissions()
    @commands.guild_only()
    async def clearinv(self, ctx, user: discord.Member):
        """Completely clears a user's inventory."""
        await ctx.send("Are you sure you want to completely wipe {}'s inventory?".format(user.name))
        choice = await ctx.bot.wait_for("message", timeout=25, check=Checks(ctx).confirm)
        if choice.content.lower() != "yes":
            return await ctx.send("Canceled inventory wipe.")
        instance = await self.get_instance(ctx=ctx, user=user)
        await instance.Inventory.clear()
        await ctx.send("Done. Inventory wiped for {}.".format(user.name))

    @shopadmin.command()
    @global_permissions()
    @commands.guild_only()
    async def manager(self, ctx, action: str):
        """Creates edits, or deletes a shop."""
        if action.lower() not in ("create", "edit", "delete"):
            return await ctx.send("Action must be create, edit, or delete.")
        instance = await self.get_instance(ctx, settings=True)
        try:
            if action.lower() == "create":
                await self.create_shop(ctx, instance)
            elif action.lower() == "edit":
                await self.edit_shop(ctx, instance)
            else:
                await self.delete_shop(ctx, instance)
        except asyncio.TimeoutError:
            await ctx.send("Shop manager timed out.")

    @shopadmin.command()
    @global_permissions()
    @commands.guild_only()
    async def item(self, ctx, action: str):
        """Creates, Deletes, and Edits items."""
        if action.lower() not in ("create", "edit", "delete"):
            return await ctx.send("Must pick create, edit, or delete.")
        instance = await self.get_instance(ctx, settings=True)
        im = ItemManager(ctx, instance)
        try:
            await im.run(action)
        except asyncio.TimeoutError:
            return await ctx.send("Request timed out. Process canceled.")

    @shopadmin.command()
    @global_permissions()
    @commands.guild_only()
    async def restock(self, ctx, amount: int, *, shop_name: str):
        """Restocks all items in a shop by a specified amount.

        This command will not restock auto items, because they are required
        to have an equal number of messages and stock.
        """
        instance = await self.get_instance(ctx, settings=True)
        shop = shop_name
        if shop not in await instance.Shops():
            return await ctx.send("That shop does not exist.")
        await ctx.send(
            "Are you sure you wish to increase the quantity of all "
            "items in {} by {}?\n*Note, this won't affect auto items.*"
            "".format(shop, amount)
        )
        try:
            choice = await ctx.bot.wait_for("message", timeout=25, check=Checks(ctx).confirm)
        except asyncio.TimeoutError:
            return await ctx.send("Response timed out.")

        if choice.content.lower() == "yes":
            async with instance.Shops() as shops:
                for item in shops[shop]["Items"].values():
                    if item["Type"] != "auto":
                        try:
                            item["Qty"] += amount
                        except TypeError:
                            continue
            await ctx.send("All items in {} have had their quantities increased by {}.".format(shop, amount))
        else:
            await ctx.send("Restock canceled.")

    @shopadmin.command()
    @global_permissions()
    @commands.guild_only()
    async def bulkadd(self, ctx, style: str, *, entry: str):
        """Add multiple items and shops.

        Bulk accepts two styles: text or a file. If you choose
        file, then the next argument is your file name.

        Files should be saved in your CogManager/cogs/shop/data path.

        If you choose text, then each line will be parsed.
        Each entry begins as a new line. All parameters MUST
        be separated by a comma.

        Parameters:
        ----------
        Shop Name, Item Name, Type, Quantity, Cost, Info, Role
        Role is only required if the type is set to role.

        Examples
        -------
        Holy Temple, Torch, basic, 20, 5, Provides some light.
        Holy Temple, Divine Training, role, 20, 500, Gives Priest role., Priest
        Junkyard, Mystery Box, random, 20, 500, Random piece of junk.

        For more information on the parameters visit the shop wiki.
        """
        if style.lower() not in ("file", "text"):
            return await ctx.send("Invalid style type. Must be file or text.")

        msg = await ctx.send("Beginning bulk upload process for shop. This may take a while...")
        instance = await self.get_instance(ctx, settings=True)
        parser = Parser(ctx, instance, msg)
        if style.lower() == "file":
            if not ctx.bot.is_owner(ctx.author):
                return await ctx.send("Only the owner can add items via csv files.")
            fp = bundled_data_path(self) / f"{entry}.csv"
            await parser.search_csv(fp)
        else:
            await parser.parse_text_entry(entry)

    @shopadmin.command(name="getdist")
    @commands.is_owner()
    async def outstanding_distributions(self,ctx):
        """Runs distribution process for Distributable items."""

        instance = await self.get_instance(ctx, settings=True)
        
        try:
            redeem_role = await instance.Settings.Redeem_Role()
            redemption_role = discord.utils.get(ctx.guild.roles, name=redeem_role)
        except:
            redemption_role = None
        try:
            announcement_id = await instance.Settings.Distribution_Channel()
            announcement_channel = discord.utils.get(ctx.guild.channels,id=announcement_id)
        except:
            announcement_channel = None

        dist_list = []
        dist_count = 0

        embed = discord.Embed(title="Outstanding Cash Items",
                            description=f"",
                            color=await ctx.embed_color())

        all_inventory = await self.config.all_members()

        for user, data in all_inventory[ctx.guild.id].items():

            user_invcount = 0
            userIns = ctx.guild.get_member(user)
            itemList = ""

            for item, item_data in data['Inventory'].items():
                if item_data['Type'] == 'distributable' or item_data['Type'] == 'redeemable':
                    itemList += f"\u200b\u3000{item_data['Qty']}x {item}\n"
                    user_invcount += 1
                    dist_count += 1

            if user_invcount > 0:
                embed.add_field(name=f"{userIns.display_name}#{userIns.discriminator}",value=itemList,inline=False)

        if dist_count > 0:
            await ctx.send(embed=embed)
        else:
            await ctx.send("No Items outstanding for distribution.")


    @shopadmin.command(name="rundist")
    @commands.is_owner()
    async def run_distribution(self,ctx):
        """Runs distribution process for Distributable items."""

        instance = await self.get_instance(ctx, settings=True)
        
        try:
            redeem_role = await instance.Settings.Redeem_Role()
            redemption_role = discord.utils.get(ctx.guild.roles, name=redeem_role)
        except:
            redemption_role = None
        try:
            announcement_id = await instance.Settings.Distribution_Channel()
            announcement_channel = discord.utils.get(ctx.guild.channels,id=announcement_id)
        except:
            announcement_channel = None

        dist_list = []
        dist_value = 0

        all_inventory = await self.config.all_members()

        for user, data in all_inventory[ctx.guild.id].items():            
            for item, item_data in data['Inventory'].items():

                if item_data['Type'] == 'distributable':
                    dist_item = [{
                        'user': user,
                        'item': item,
                        'item_data': item_data,
                        }]
                    dist_item = dist_item * item_data['Qty']
                    dist_list.extend(dist_item)

        if len(dist_list) > 0:
            while len(dist_list) > 0 and dist_value < 20:
                
                random.shuffle(dist_list)
                distItem = dist_list.pop(random.randint(0,len(dist_list)-1))

                distItem['item_data']['Type'] = 'redeemable'
                recipient = ctx.guild.get_member(distItem['user'])

                if distItem['item_data']['cashType'] == "nitro":
                    if distItem['item_data']['cashValue'] == '1Y':
                        distItemValue = 100
                        distItemName = f"{prefix[distItem['item_data']['Type']]} {itemCashType[distItem['item_data']['cashType']]} - 1 Year"
                    else:
                        distItemValue = int(distItem['item_data']['cashValue'])
                        distItemName = f"{prefix[distItem['item_data']['Type']]} {itemCashType[distItem['item_data']['cashType']]} - {distItem['item_data']['cashValue']} Month(s)"

                elif distItem['item_data']['cashType'] == "goldpass":
                    distItemValue = 5
                    distItemName = f"{prefix[distItem['item_data']['Type']]} COC Gold Pass - USD5 Gift Card"

                else:
                    distItemValue = int(distItem['item_data']['cashValue'])
                    distItemName = f"{prefix[distItem['item_data']['Type']]} {itemCashType[distItem['item_data']['cashType']]} - USD {distItem['item_data']['cashValue']}"

                recipient_ins = await self.get_instance(ctx,user=recipient)
                sm = ShopManager(ctx,None,recipient_ins)

                await sm.remove(item=distItem['item'],number=1)
                await sm.add(item=f"{distItemName}",data=distItem['item_data'],quantity=1)
                
                if redemption_role:
                    await recipient.add_roles(redemption_role)
                if announcement_channel:
                    await announcement_channel.send(content=f"{recipient.mention} is now able to redeem 1x **{distItemName}**!")
                dist_value += distItemValue

    @shopadmin.command(name="runcleanup")
    @commands.is_owner()
    async def run_cleanup(self,ctx):
        """Runs distribution process for Distributable items."""

        instance = await self.get_instance(ctx, settings=True)
        
        try:
            redeem_role = await instance.Settings.Redeem_Role()
            redemption_role = discord.utils.get(ctx.guild.roles, name=redeem_role)
        except:
            redemption_role = None        

        all_user_data = await self.config.all_members()

        for user_id, data in all_user_data[ctx.guild.id].items():
            member = ctx.guild.get_member(user_id)
            if member == None:
                memberPlaceholder = namedtuple("placeholder","id guild")
                member = memberPlaceholder(user_id,ctx.guild)
                await self.config.member(member).clear()

        if redemption_role:
            members_with_redemption = redemption_role.members

            for member in members_with_redemption:
                member_has_redemption = False
                member_data = await self.get_instance(ctx, user=member)

                if not await member_data.Inventory():
                    await member.remove_roles(redemption_role)

                else:
                    inventory_data = await member_data.Inventory.all()
                
                    for item, item_data in inventory_data.items():
                        if item_data['Type'] == 'redeemable':
                            member_has_redemption = True

                    if not member_has_redemption:
                        await member.remove_roles(redemption_role)

        return await ctx.send("Clean up completed.")

    # -----------------------------------------------------------------------------

    @commands.group(autohelp=True)
    async def setshop(self, ctx):
        """Shop Settings group command"""
        pass

    @setshop.command()
    @commands.is_owner()
    async def mode(self, ctx):
        """Toggles Shop between global and local modes.

        When shop is set to local mode, each server will have its own
        unique data, and admin level commands can be used on that server.

        When shop is set to global mode, data is linked between all servers
        the bot is connected to. In addition, admin level commands can only be
        used by the owner or co-owners.
        """
        author = ctx.author
        mode = "global" if await self.shop_is_global() else "local"
        alt = "local" if mode == "global" else "global"
        await ctx.send(
            "Shop is currently set to {} mode. Would you like to change to {} mode instead?".format(mode, alt)
        )
        checks = Checks(ctx)
        try:
            choice = await ctx.bot.wait_for("message", timeout=25.0, check=checks.confirm)
        except asyncio.TimeoutError:
            return await ctx.send("No response. Action canceled.")

        if choice.content.lower() != "yes":
            return await ctx.send("Shop will remain {}.".format(mode))
        await ctx.send(
            "Changing shop to {0} will **DELETE ALL** current shop data. Are "
            "you sure you wish to make shop {0}?".format(alt)
        )
        try:
            final = await ctx.bot.wait_for("message", timeout=25.0, check=checks.confirm)
        except asyncio.TimeoutError:
            return await ctx.send("No response. Action canceled.")

        if final.content.lower() == "yes":
            await self.change_mode(alt)
            log.info("{} ({}) changed the shop mode to {}.".format(author.name, author.id, alt))
            await ctx.send("Shop data deleted! Shop mode is now set to {}.".format(alt))
        else:
            await ctx.send("Shop will remain {}.".format(mode))

    # @setshop.command()
    # @global_permissions()
    # @commands.guild_only()
    # async def alertrole(self, ctx, role: discord.Role):
    #     """Sets the role that will receive alerts.

    #     Alerts will be sent to any user who has this role on the server. If
    #     the shop is global, then the owner will receive alerts regardless
    #     of their role, until they turn off alerts.
    #     """
    #     if role.name == "Bot":
    #         return
    #     instance = await self.get_instance(ctx, settings=True)
    #     await instance.Settings.Alert_Role.set(role.name)
    #     await ctx.send("Alert role has been set to {}.".format(role.name))

    @setshop.command()
    @global_permissions()
    @commands.guild_only()
    async def redeemrole(self, ctx, role: discord.Role):
        """Sets the role that is allowed to redeem items."""
        if role.name == "Bot":
            return
        instance = await self.get_instance(ctx, settings=True)
        await instance.Settings.Redeem_Role.set(role.name)
        await ctx.send("Redeem role has been set to {}.".format(role.name))

    @setshop.command()
    @global_permissions()
    @commands.guild_only()
    async def redemptionchannel(self, ctx, channel_id=0):
        """Sets the role that is allowed to redeem items."""

        instance = await self.get_instance(ctx, settings=True)
        await instance.Settings.Distribution_Channel.set(channel_id)
        await ctx.send("The Announcement Channel for Distributions has been set to <#{}>.".format(channel_id))

    # @setshop.command()
    # @global_permissions()
    # @commands.guild_only()
    # async def alerts(self, ctx):
    #     """Toggles alerts when users redeem items."""
    #     instance = await self.get_instance(ctx, settings=True)
    #     status = await instance.Settings.Alerts()
    #     await instance.Settings.Alerts.set(not status)
    #     await ctx.send("Alert role will {} messages.".format("no longer" if status else "receive"))

    @setshop.command()
    @global_permissions()
    @commands.guild_only()
    async def sorting(self, ctx, style: str):
        """Set how shop items are sorted.

        Options: price, quantity, or name (alphabetical)
        By default shops are ordered by price."""
        instance = await self.get_instance(ctx, settings=True)
        if style not in ("price", "quantity", "name"):
            return await ctx.send("You must pick a valid sorting option: `price`, `quantity`, or `name`.")
        await instance.Settings.Sorting.set(style.lower())
        await ctx.send(f"Shops will now be sorted by {style}.")

    #@setshop.command()
    #@global_permissions()
    #@commands.guild_only()
    #async def gifting(self, ctx):
    #    """Toggles if users can gift items."""
    #    instance = await self.get_instance(ctx, settings=True)
    #    status = await instance.Settings.Gifting()
    #    await instance.Settings.Gifting.set(not status)
    #    await ctx.send(f"Gifting is now {'OFF'} if status else {'ON'}.")

    @setshop.command()
    @global_permissions()
    @commands.guild_only()
    async def toggle(self, ctx):
        """Closes/opens all shops."""
        instance = await self.get_instance(ctx, settings=True)
        status = await instance.Settings.Closed()
        await instance.Settings.Closed.set(not status)
        await ctx.send("Shops are now {}.".format("open" if status else "closed"))

    # -------------------------------------------------------------------------------

    @staticmethod
    async def check_availability(ctx, shops):
        if ctx.guild:
            perms = ctx.author.guild_permissions.administrator
            author_roles = [r.name for r in ctx.author.roles]
            return [x for x, y in shops.items() if (y["Role"] in author_roles or perms) and y["Items"]]

    @staticmethod
    async def clear_single_pending(ctx, instance, data, item, user):
        item_name = data[str(user.id)][item]["Item"]
        await ctx.send(
            "You are about to clear a pending {} for {}.\nAre you sure "
            "you wish to clear this item?".format(item_name, user.name)
        )
        choice = await ctx.bot.wait_for("message", timeout=25, check=Checks(ctx).confirm)
        if choice.content.lower() == "yes":
            async with instance.Pending() as p:
                del p[str(user.id)][item]
                if not p[str(user.id)]:
                    del p[str(user.id)]
            await ctx.send("{} was cleared from {}'s pending by {}.".format(item_name, user.name, ctx.author.name))
            await user.send("{} cleared your pending {}!".format(ctx.author.name, item_name))
        else:
            await ctx.send("Action canceled.")

    @staticmethod
    async def clear_all_pending(ctx, instance, user):
        await ctx.send("You are about to clear all pending items from {}.\nAre you sure you wish to do this?")
        choice = await ctx.bot.wait_for("message", timeout=25, check=Checks(ctx).confirm)
        if choice.content.lower() == "yes":
            async with instance.Pending() as p:
                del p[user.id]
            await ctx.send("All pending items have been cleared for {}.".format(user.name))
            await user.send("{} cleared **ALL** of your pending items.".format(ctx.author.name))
        else:
            await ctx.send("Action canceled.")

    async def get_instance(self, ctx, settings=False, user=None):
        if not user:
            user = ctx.author

        if await self.config.Global():
            if settings:
                return self.config
            else:
                return self.config.user(user)
        else:
            if settings:
                return self.config.guild(ctx.guild)
            else:
                return self.config.member(user)

    async def assign_role(self, ctx, instance, item, role_name):
        if await self.config.Global():
            if not ctx.guild:
                return await ctx.send(
                    "Unable to assign role, because shop is in global mode."
                    "Try redeeming your item in a server instead of in DMs."
                )

        role = discord.utils.get(ctx.message.guild.roles, name=role_name)
        if role is None:
            return await ctx.send(
                "Could not assign the role, {}, because it does not exist on the server.".format(role_name)
            )
        try:
            await ctx.author.add_roles(role, reason="Shop role token was redeemed.")
        except discord.Forbidden:
            await ctx.send(
                "The bot could not add this role because it does not have the "
                "permission to do so. Make sure the bot has the permissions enabled and "
                "its role is higher than the role that needs to be assigned."
            )
            return False
        sm = ShopManager(ctx, None, instance)
        await sm.remove(item)
        await ctx.send("{} was granted the {} role.".format(ctx.author.mention, role.name))

    async def pending_add(self, ctx, item):
        instance = await self.get_instance(ctx, settings=True)
        unique_id = str(uuid.uuid4())[:17]
        timestamp = ctx.message.created_at.now().strftime("%Y-%m-%d %H:%M:%S")
        async with instance.Pending() as p:
            if str(ctx.author.id) in p:
                p[str(ctx.author.id)][unique_id] = {"Item": item, "Timestamp": timestamp}
            else:
                p[str(ctx.author.id)] = {unique_id: {"Item": item, "Timestamp": timestamp}}
        msg = "{} added {} to your pending list.".format(ctx.author.mention, item)
        if await instance.Settings.Alerts():
            alert_role = await instance.Settings.Alert_Role()
            role = discord.utils.get(ctx.guild.roles, name=alert_role)
            if role:
                msg = "{}\n{}".format(role.mention, msg)
        await ctx.send(msg)

    async def change_mode(self, mode):
        await self.config.clear_all()
        if mode == "global":
            await self.config.Global.set(True)

    async def shop_is_global(self):
        return await self.config.Global()

    async def edit_shop(self, ctx, instance):
        shops = await instance.Shops.all()
        await ctx.send("What shop would you like to edit?")
        name = await ctx.bot.wait_for("message", timeout=25, check=Checks(ctx, custom=shops).content)

        await ctx.send("Would you like to change the shop's `name` or `role` requirement?")
        choice = await ctx.bot.wait_for("message", timeout=25, check=Checks(ctx, custom=("name", "role")).content)
        if choice.content.lower() == "name":
            await ctx.send("What is the new name for this shop?")
            new_name = await ctx.bot.wait_for("message", timeout=25, check=Checks(ctx, length=25).length_under)
            async with instance.Shops() as shops:
                shops[new_name.content] = shops.pop(name.content)
            return await ctx.send("Name changed to {}.".format(new_name.content))
        else:
            await ctx.send("What is the new role for this shop?")
            role = await ctx.bot.wait_for("message", timeout=25, check=Checks(ctx).role)
            async with instance.Shops() as shops:
                shops[name.content]["Role"] = role.content
            await ctx.send("{} is now restricted to only users with the {} role.".format(name.content, role.content))

    async def delete_shop(self, ctx, instance):
        shops = await instance.Shops.all()
        await ctx.send("What shop would you like to delete?")
        name = await ctx.bot.wait_for("message", timeout=25, check=Checks(ctx, custom=shops).content)
        await ctx.send("Are you sure you wish to delete {} and all of its items?".format(name.content))
        choice = await ctx.bot.wait_for("message", timeout=25, check=Checks(ctx).confirm)

        if choice.content.lower() == "no":
            return await ctx.send("Shop deletion canceled.")
        async with instance.Shops() as shops:
            del shops[name.content]
        await ctx.send("{} was deleted.".format(name.content))

    async def create_shop(self, ctx, instance):
        await ctx.send("What is the name of this shop?\nName must be 25 characters or less.")
        name = await ctx.bot.wait_for("message", timeout=25, check=Checks(ctx, length=25).length_under)

        if name.content.startswith(ctx.prefix):
            return await ctx.send("Closing shop creation. Please don't run commands while attempting to create a shop.")

        if name.content in await instance.Shops():
            return await ctx.send("A shop with this name already exists.")

        msg = (
            "What role can use this shop? Use `all` for everyone.\n"
            "*Note: this role must exist on this server and is case sensitive.*\n"
        )
        if await self.shop_is_global():
            msg += (
                "Shop is also currently in global mode. If you choose to restrict "
                "this shop to a role that is on this server, the shop will only be "
                "visible on this server to people with the role."
            )
        await ctx.send(msg)

        def predicate(m):
            if m.author == ctx.author:
                if m.content in [r.name for r in ctx.guild.roles]:
                    return True
                elif m.content.lower() == "all":
                    return True
                else:
                    return False
            else:
                return False

        try:
            role = await ctx.bot.wait_for("message", timeout=25.0, check=predicate)
        except asyncio.TimeoutError:
            return await ctx.send("Response timed out. Shop creation ended.")

        role_name = role.content if role.content != "all" else "@everyone"
        async with instance.Shops() as shops:
            shops[name.content] = {"Items": {}, "Role": role_name}
        await ctx.send(
            "Added {} to the list of shops.\n"
            "**NOTE:** This shop will not show up until an item is added to it's "
            "list.".format(name.content)
        )

    async def pending_prompt(self, ctx, instance, data, item):
        e = discord.Embed(color=await ctx.embed_colour())
        e.add_field(name=item, value=data[item]["Info"], inline=False)
        if data[item]["Type"].lower() == "Role":
            await ctx.send(
                "{} Do you wish to redeem {}? This will grant you the role assigned to "
                "this item and it will be removed from your inventory "
                "permanently.".format(ctx.author.mention, item),
                embed=e,
            )
        else:
            await ctx.send(
                "{} Do you wish to redeem {}? This will add the item to the pending "
                "list for an admin to review and grant. The item will be removed from "
                "your inventory while this is "
                "processing.".format(ctx.author.mention, item),
                embed=e,
            )
        try:
            choice = await ctx.bot.wait_for("message", timeout=25, check=Checks(ctx).confirm)
        except asyncio.TimeoutError:
            return await ctx.send("No Response. Item redemption canceled.")

        if choice.content.lower() != "yes":
            return await ctx.send("Canceled item redemption.")

        if data[item]["Type"].lower() == "role":
            return await self.assign_role(ctx, instance, item, data[item]["Role"])
        else:
            await self.pending_add(ctx, item)

        sm = ShopManager(ctx, instance=None, user_data=instance)
        await sm.remove(item)


class ShopManager:
    def __init__(self, ctx, instance, user_data):
        self.ctx = ctx
        self.instance = instance
        self.user_data = user_data

    @staticmethod
    def weighted_choice(choices):
        """Stack Overflow: https://stackoverflow.com/a/4322940/6226473"""
        values, weights = zip(*choices)
        total = 0
        cum_weights = []
        for w in weights:
            total += w
            cum_weights.append(total)
        x = random.random() * total
        i = bisect(cum_weights, x)
        return values[i]

    async def random_item(self, shop):
        async with self.instance.Shops() as shops:
            try:
                return self.weighted_choice(
                    [(x, y["Cost"]) for x, y in shops[shop]["Items"].items() if y["Type"] != "random"]
                )
            except IndexError:
                return

    async def auto_handler(self, shop, item, amount):
        async with self.instance.Shops() as shops:
            msgs = [shops[shop]["Items"][item]["Messages"].pop() for _ in range(amount)]
        msg = "\n".join(msgs)
        if len(msg) < 2000:
            await self.ctx.author.send(msg)
        else:
            chunks = textwrap.wrap(msg, 2000)
            for chunk in chunks:
                await asyncio.sleep(2)  # At least a little buffer to prevent rate limiting
                await self.ctx.author.send(chunk)

    async def order(self, shop, item):
        user = self.ctx.author
        try:
            async with self.instance.Shops() as shops:
                shop_items = deepcopy(shops[shop]["Items"])
                item_data = deepcopy(shops[shop]["Items"][item])
        except KeyError:
            return await self.ctx.send("Could not locate that shop or item.")

        cur = await bank.get_currency_name(self.ctx.guild)
        stock, cost, _type = item_data["Qty"], item_data["Cost"], item_data["Type"]

        if _type == 'role':
            for user_role in self.ctx.author.roles:
                if user_role.name == item_data['Role']:
                    return await self.ctx.send(f"{self.ctx.author.mention}, you already have the **{item_data['Role']}** role.")

        ask_qty = True
        if _type == 'role' or _type == 'aitem':
            ask_qty = False
        if _type == 'random':
            ask_qty = False

        e = discord.Embed(color=await self.ctx.embed_colour())
        e.add_field(name=item, value=item_data["Info"], inline=False)
        if ask_qty:
            text = (
                f"How many {item} would you like to purchase?"
            )
        else:
            text = (
                f"Reply with 'yes' to confirm your purchase of {item}.\n\n**To cancel, reply with 'exit' or 'cancel'.**"
            )
        await self.ctx.send(content=text, embed=e)

        def predicate(m):
            if m.author == self.ctx.author and self.ctx.channel == m.channel:
                if m.content.lower() in ("exit", "cancel", "e", "x"):
                    return True
                elif ask_qty and m.content.isdigit():
                    try:
                        return 0 < int(m.content) <= stock
                    except TypeError:
                        return 0 < int(m.content)
                elif not ask_qty and m.content.lower() in ("yes"):
                    return True
                else:
                    return False
            else:
                return False

        num = await self.ctx.bot.wait_for("message", timeout=25.0, check=predicate)
        if num.content.lower() in ("exit", "cancel", "e", "x"):
            raise ExitProcess()
        if ask_qty:
            amount = int(num.content)
        else:
            amount = 1
        try:
            await num.delete()
        except (discord.NotFound, discord.Forbidden):
            pass

        cost *= amount
        try:
            await bank.withdraw_credits(self.ctx.author, cost)
        except ValueError:
            return await self.ctx.send(
                "You cannot afford {}x {} for {} {}. Transaction ended.".format(num.content, item, cost, cur)
            )
        im = ItemManager(self.ctx, self.instance)
        if _type == "auto":
            await self.auto_handler(shop, item, amount)
            await im.remove(shop, item, stock, amount)
            return await self.ctx.send("Message sent.")

        if _type == "role":
            if shop == "Discord Color Store":
                for item_r in shop_items.values():
                    try:
                        remove_role = discord.utils.get(self.ctx.guild.roles, name=item_r['Role'])
                        await self.ctx.author.remove_roles(remove_role)
                    except:
                        pass

            target_role = discord.utils.get(self.ctx.guild.roles, name=item_data["Role"])
            await self.ctx.author.add_roles(target_role)
            return await self.ctx.send(f"{self.ctx.author.mention}, you've received the **{item_data['Role']}** role!")

        if _type == "achest":
            acog = self.ctx.bot.get_cog("Adventure")
            crarity = item_data["cRarity"]

            async with Adventure.get_lock(acog,user):
                try:
                    c = await Character.from_json(self.ctx, acog.config, user, acog._daily_bonus)
                except:
                    return await self.ctx.send("Error getting character info.")

                if crarity == "rare":
                    c.treasure[1] += (amount*10)
                elif crarity == "epic":
                    c.treasure[2] += (amount*10)
                elif crarity == "legendary":
                    c.treasure[3] += (amount*10)
                elif crarity == "ascended":
                    c.treasure[4] += (amount*10)
                elif crarity == "set":
                    c.treasure[5] += (amount*10)
                else:
                    c.treasure[0] += (amount*10)

                await acog.config.user(user).set(await c.to_json(self.ctx,acog.config))
                return await self.ctx.send(f"{self.ctx.author.mention}, you've received {amount*10} {crarity.capitalize()} Chests in Adventure.")

        if _type == "aitem":
            acog = self.ctx.bot.get_cog("Adventure")
            item_astats = item_data['aItemStats']

            item_name = f"{self.ctx.author.name}'s {item}"
            adventure_item = {item: item_astats}
            adventure_item_grant = Item.from_json(self.ctx, adventure_item)
            async with Adventure.get_lock(acog,user):
                try:
                    c = await Character.from_json(self.ctx, acog.config, user, acog._daily_bonus)
                except:
                    return await self.ctx.send("Error getting character info.")
                await c.add_to_backpack(adventure_item_grant)
                await acog.config.user(user).set(await c.to_json(self.ctx,acog.config))
                return await self.ctx.send(f"{self.ctx.author.mention}, you've received {adventure_item_grant} in your Backpack.")

        if _type == "random":
            new_item = await self.random_item(shop)
            if new_item is None:
                try:
                    await bank.deposit_credits(self.ctx.author, cost)
                except BalanceTooHigh as e:
                    await bank.set_balance(self.ctx.author, e.max_balance)
                return await self.ctx.send(
                    "There aren't any non-random items available in {}, "
                    "so {} cannot be purchased.".format(shop, item)
                )
            else:
                await im.remove(shop, item, stock, amount)
                item = new_item
                async with self.instance.Shops() as shops:
                    item_data = deepcopy(shops[shop]["Items"][new_item])
                stock = item_data["Qty"]

        await im.remove(shop, item, stock, amount)
        await self.add(item, item_data, amount)
        await self.ctx.send("{} purchased {}x {} for {} {}.".format(self.ctx.author.mention, amount, item, cost, cur))

    async def add(self, item, data, quantity):
        async with self.user_data.Inventory() as inv:
            if item in inv:
                inv[item]["Qty"] += quantity
            else:
                inv[item] = data
                inv[item]["Qty"] = quantity

    async def remove(self, item, number=1):
        async with self.user_data.Inventory() as inv:
            if number >= inv[item]["Qty"]:
                del inv[item]
            else:
                inv[item]["Qty"] -= number


class ItemManager:
    def __init__(self, ctx, instance):
        self.ctx = ctx
        self.instance = instance

    async def run(self, action):

        if action.lower() == "create":
            await self.create()
        elif action.lower() == "edit":
            await self.edit()
        else:
            await self.delete()

    async def create(self):
        name = await self.set_name()
        if not name:
            return
        cost = await self.set_cost()
        info = await self.set_info()
        _type, role, msgs, crarity, itemstats, cashType, cashValue = await self.set_type()
        if _type != "auto":
            qty = await self.set_quantity(_type)
        else:
            qty = len(msgs)

        data = {
            "Cost": cost,
            "Qty": qty,
            "Type": _type,
            "Info": info,
            "Role": role,
            "Messages": msgs,
            "cRarity": crarity,
            "aItemStats": itemstats,
            "cashType": cashType,
            "cashValue": cashValue,
        }

        if _type == 'distributable' or _type == 'redeemable':
            if cashType == 'nitro':
                if cashValue == '1Y':
                    name = f"{prefix[_type]} {itemCashType[cashType]} - 1 Year"
                else:
                    name = f"{prefix[_type]} {itemCashType[cashType]} - {cashValue} Month(s)"
            if cashType == 'goldpass':
                name = f"{prefix[_type]} COC Gold Pass - USD5 Gift Card"
            else:
                name = f"{prefix[_type]} {itemCashType[cashType]} - USD {cashValue}"

        msg = "What shop would you like to add this item to?\n"
        shops = await self.instance.Shops()
        msg += "Current shops are: "
        msg += humanize_list([f"`{shopname}`" for shopname in sorted(shops.keys())])
        await self.ctx.send(msg)
        shop = await self.ctx.bot.wait_for("message", timeout=25, check=Checks(self.ctx, custom=shops.keys()).content)
        await self.add(data, shop.content, name)
        await self.ctx.send("Item creation complete. Check your logs to ensure it went to the approriate shop.")

    async def delete(self):
        shop_list = await self.instance.Shops.all()

        def predicate(m):
            if self.ctx.author != m.author:
                return False
            return m.content in shop_list

        await self.ctx.send("What shop would you like to delete an item from?")
        shop = await self.ctx.bot.wait_for("message", timeout=25, check=predicate)

        def predicate2(m):
            if self.ctx.author != m.author:
                return False
            return m.content in shop_list[shop.content]["Items"]

        await self.ctx.send("What item would you like to delete from this shop?")
        item = await self.ctx.bot.wait_for("message", timeout=25, check=predicate2)
        await self.ctx.send("Are you sure you want to delete {} from {}?".format(item.content, shop.content))
        choice = await self.ctx.bot.wait_for("message", timeout=25, check=Checks(self.ctx).confirm)
        if choice.content.lower() == "yes":
            async with self.instance.Shops() as shops:
                del shops[shop.content]["Items"][item.content]
            await self.ctx.send("{} was deleted from the {}.".format(item.content, shop.content))
        else:
            await self.ctx.send("Item deletion canceled.")

    async def edit(self):
        choices = ("name", "type", "role", "qty", "cost", "msgs", "quantity", "info", "messages", "rarity")

        while True:
            shop, item, item_data = await self.get_item()

            def predicate(m):
                if self.ctx.author != m.author:
                    return False
                if m.content.lower() in ("msgs", "messages") and item_data["Type"] != "auto":
                    return False
                elif m.content.lower() in ("qty", "quantity") and item_data["Type"] == "auto":
                    return False
                elif m.content.lower() == "role" and item_data["Type"] != "role":
                    return False
                elif m.content.lower() == "rarity" and item_data["Type"] != "achest":
                    return False
                elif m.content.lower() not in choices:
                    return False
                else:
                    return True

            await self.ctx.send(
                "What would you like to edit for this item?\n"
                "`Name`, `Type`, `Role`, `Quantity`, `Cost`, `Info`, `Messages`, or 'Rarity'?\n"
                "Note that `Messages` cannot be edited on non-auto type items."
            )
            choice = await self.ctx.bot.wait_for("message", timeout=25.0, check=predicate)

            if choice.content.lower() == "name":
                await self.set_name(item=item, shop=shop)
            elif choice.content.lower() == "type":
                await self.set_type(item, shop)
            elif choice.content.lower() == "role":
                await self.set_role(item, shop)
            elif choice.content.lower() in ("qty", "quantity"):
                await self.set_quantity(item_data["Type"], item, shop)
            elif choice.content.lower() == "cost":
                await self.set_cost(item=item, shop=shop)
            elif choice.content.lower() == "info":
                await self.set_info(item=item, shop=shop)
            elif choice.content.lower() == "rarity":
                await self.set_rarity(item=item, shop=shop)
            else:
                await self.set_messages(item_data["Type"], item=item, shop=shop)

            await self.ctx.send("Would you like to continue editing?")
            rsp = await self.ctx.bot.wait_for("message", timeout=25, check=Checks(self.ctx).confirm)
            if rsp.content.lower() == "no":
                await self.ctx.send("Editing process exited.")
                break

    async def set_messages(self, _type, item=None, shop=None):
        if _type != "auto":
            return await self.ctx.send("You can only add messages to auto type items.")
        await self.ctx.send(
            "Auto items require a message to be stored per quantity. Separate each "
            "message with a new line using a code block."
        )
        msgs = await self.ctx.bot.wait_for("message", timeout=120, check=Checks(self.ctx).same)
        auto_msgs = [x.strip() for x in msgs.content.strip("`").split("\n") if x]
        if item:
            async with self.instance.Shops() as shops:
                shops[shop]["Items"][item]["Messages"].extend(auto_msgs)
                shops[shop]["Items"][item]["Qty"] += len(auto_msgs)
            return await self.ctx.send("{} messages were added to {}.".format(len(auto_msgs), item))
        return auto_msgs

    async def set_name(self, item=None, shop=None):
        await self.ctx.send("Enter a name for this item. It can't be longer than 20 characters.")
        name = await self.ctx.bot.wait_for("message", timeout=25, check=Checks(self.ctx, length=30).length_under)

        if name.content.startswith(self.ctx.prefix):
            await self.ctx.send("Closing item creation. Please don't run commands while attempting to create an item.")
            return None

        if item:
            async with self.instance.Shops() as shops:
                shops[shop]["Items"][name.content] = shops[shop]["Items"].pop(item)
            return await self.ctx.send("{}'s name was changed to {}.".format(item, name.content))
        return name.content

    async def set_cost(self, item=None, shop=None):
        await self.ctx.send("Enter the new cost for this item.")
        cost = await self.ctx.bot.wait_for("message", timeout=25, check=Checks(self.ctx).positive)

        if item:
            async with self.instance.Shops() as shops:
                shops[shop]["Items"][item]["Cost"] = int(cost.content)
            return await self.ctx.send("This item now costs {}.".format(cost.content))
        return int(cost.content)

    def hierarchy_check(self, m):
        roles = [r.name for r in self.ctx.guild.roles if r.name != "Bot"]
        if self.ctx.author == m.author and m.content in roles:
            if self.ctx.author.top_role >= discord.utils.get(self.ctx.message.guild.roles, name=m.content):
                return True
            else:
                return False
        else:
            return False

    async def set_role(self, item=None, shop=None):
        await self.ctx.send(
            "What role do you wish for this item to assign?\n*Note, you cannot add a role higher than your own.*"
        )
        role = await self.ctx.bot.wait_for("message", timeout=25, check=self.hierarchy_check)
        if item:
            async with self.instance.Shops() as shops:
                shops[shop]["Items"][item]["Role"] = role.content
            return await self.ctx.send("This item now assigns the {} role.".format(role.content))
        return role.content

    def rarity_check(self, m):
        valid_rarity = ["normal", "rare", "epic", "legendary", "ascended", "set"]
        if m.content.lower() in valid_rarity:
            return True
        else:
            return False

    async def set_rarity(self, item=None, shop=None):
        await self.ctx.send(
            "What Chest Rarity should this item grant? Acceptable rarities: 'Normal', 'Rare', 'Epic', 'Legendary', 'Ascended', 'Set'"
        )

        rarity = await self.ctx.bot.wait_for("message", timeout=25, check=self.rarity_check)
        if item:
            async with self.instance.Shops() as shops:
                shops[shop]["Items"][item]["cRarity"] = rarity.content.lower()
            return await self.ctx.send("This item now grants {} chest in Adventure.".format(rarity.content.lower()))
        return rarity.content.lower()

    def slot_check(self, m):
        valid_slots = [
            "head",
            "neck",
            "chest",
            "gloves",
            "belt",
            "legs",
            "boots",
            "left",
            "right",
            "two handed",
            "ring",
            "charm",
            ]
        if m.content.lower() in valid_slots:
            return True
        else:
            return False

    async def set_itemstats(self, item=None, shop=None):

        await self.ctx.send(
            "Please specify the Slot of this item. Acceptable slots: head, neck, chest, gloves, belt, legs, boots, left, right, two handed, ring, charm."
        )
        slot_list = []
        slot = await self.ctx.bot.wait_for("message", timeout=25, check=self.slot_check)
        slot_list.append(slot.content.lower())

        await self.ctx.send(
            "Please specify the ATT stat of this item."
        )
        att = await self.ctx.bot.wait_for("message", timeout=25)

        await self.ctx.send(
            "Please specify the CHA stat of this item."
        )
        cha = await self.ctx.bot.wait_for("message", timeout=25)

        await self.ctx.send(
            "Please specify the INT stat of this item."
        )
        intel = await self.ctx.bot.wait_for("message", timeout=25)

        await self.ctx.send(
            "Please specify the DEX stat of this item."
        )
        dex = await self.ctx.bot.wait_for("message", timeout=25)

        await self.ctx.send(
            "Please specify the LUCK stat of this item."
        )
        luck = await self.ctx.bot.wait_for("message", timeout=25)

        await self.ctx.send(
            "Please specify the DEGRADE value of this item."
        )
        degrade = await self.ctx.bot.wait_for("message", timeout=25)

        await self.ctx.send(
            "Please specify the LEVEL requirement of this item."
        )
        level = await self.ctx.bot.wait_for("message", timeout=25)

        itemStatsDict = {
            "att": int(att.content),
            "cha": int(cha.content),
            "int": int(intel.content),
            "dex": int(dex.content),
            "luck": int(luck.content),
            "rarity": "event",
            "slot": slot_list,
            "degrade": int(degrade.content),
            "lvl": int(level.content),
            }

        if item:
            async with self.instance.Shops() as shops:
                shops[shop]["Items"][item]["aItemStats"] = itemStatsDict
            return await self.ctx.send("This item now grants {} in Adventure.".format(itemStatsDict))
        return itemStatsDict

    def cashtype_check(self, m):
        valid_cashtype = ["giftcard","goldpass"]
        if m.content.lower() in valid_cashtype:
            return True
        else:
            return False

    async def set_cashtype(self, item=None, shop=None):
        await self.ctx.send(
            "```What Cash Type should this item grant?```Valid types: GiftCard or GoldPass."
        )
        cashtype = await self.ctx.bot.wait_for("message", timeout=25, check=self.cashtype_check)
        if item:
            async with self.instance.Shops() as shops:
                shops[shop]["Items"][item]["cashType"] = cashtype.content.lower()
            return await self.ctx.send("This item now references {} Cash Type".format(cashtype.content.lower()))
        return cashtype.content.lower()

    async def set_cashvalue(self, item=None, shop=None, type=None):

        def nitro_check(m):
            return m.author == self.ctx.author and m.content.isdigit() and int(m.content) >= 0 and int(m.content) <= 12
        def giftcard_check(m):
            return m.author == self.ctx.author and m.content.isdigit() and int(m.content) >= 0 and int(m.content) <= 50

        if type == 'nitro':
            await self.ctx.send(
                "```How many months of Nitro does this provide? Max of 12 months.```"
            )
            nitroValue = await self.ctx.bot.wait_for("message", timeout=25, check=nitro_check)
            if int(nitroValue.content) == 12:
                cashValue = "1Y"
            else:
                cashValue = str(nitroValue.content)
            if item:
                async with self.instance.Shops() as shops:
                    shops[shop]["Items"][item]["cashValue"] = cashValue
                return await self.ctx.send("This item now provides {} of Discord Nitro.".format(cashValue))
            return cashValue

        if type == 'giftcard':
            await self.ctx.send(
                "```What's the value of this Gift Card? In USD. Max of 50.```"
            )
            giftcardValue = await self.ctx.bot.wait_for("message", timeout=25, check=giftcard_check)
            if item:
                async with self.instance.Shops() as shops:
                    shops[shop]["Items"][item]["cashValue"] = str(giftcardValue.content)
                return await self.ctx.send("This Gift Card item is worth USD {}.".format(giftcardValue.content))
            return str(giftcardValue.content)

        if type == 'goldpass':
            return str("5")

    async def set_quantity(self, _type=None, item=None, shop=None):
        if _type == "auto":
            return await self.ctx.send(
                "You can't change the quantity of an auto item. The quantity will match the messages set."
            )

        await self.ctx.send("What quantity do you want to set this item to?\nType 0 for infinite.")

        def check(m):
            return m.author == self.ctx.author and m.content.isdigit() and int(m.content) >= 0

        qty = await self.ctx.bot.wait_for("message", timeout=25, check=check)
        qty = int(qty.content) if int(qty.content) > 0 else "--"
        if item:
            async with self.instance.Shops() as shops:
                shops[shop]["Items"][item]["Qty"] = qty
            return await self.ctx.send(
                "Quantity for {} now set to {}.".format(item, "infinite." if qty == "--" else qty)
            )
        return qty

    async def set_type(self, item=None, shop=None):
        valid_types = ("basic", "random", "auto", "role", "achest", "aitem", "distributable", "redeemable")
        await self.ctx.send(
            "What is the item type?\n"
            "```\n"
            "basic  - Normal item and is added to the pending list when redeemed.\n"
            "random - Picks a random item in the shop, weighted on cost.\n"
            "role   - Grants a role when redeemed.\n"
            "auto   - DM's a msg to the user instead of adding to their inventory.\n"
            "achest - Distributes adventure chests.\n"
            "aitem - Grants a special item in Adventure.\n"
            "distributable - Cash Item distributable. A Cash item that is pending distribution (i.e. not redeemable yet).\n"
            "redeemable - Cash Item redeemable. A Cash item that can be redeemed and claimed."
            "```"
        )
        _type = await self.ctx.bot.wait_for("message", timeout=25, check=Checks(self.ctx, custom=valid_types).content)

        if _type.content.lower() == "auto":
            msgs = await self.set_messages("auto", item=item, shop=shop)
            if not item:
                return "auto", None, msgs, None, None, None, None

        elif _type.content.lower() == "role":
            role = await self.set_role(item=item, shop=shop)
            if not item:
                return "role", role, None, None, None, None, None

        elif _type.content.lower() == "achest":
            rarity = await self.set_rarity(item=item, shop=shop)
            if not item:
                return "achest", None, None, rarity, None, None, None

        elif _type.content.lower() == "aitem":
            stats = await self.set_itemstats(item=item, shop=shop)
            if not item:
                return "aitem", None, None, None, stats, None, None

        elif _type.content.lower() == "distributable" or _type.content.lower() == "redeemable":
            cashType = await self.set_cashtype(item=item, shop=shop)
            cashValue = await self.set_cashvalue(item=item, shop=shop, type=cashType)
            if not item:
                return _type.content.lower(), None, None, None, None, cashType, cashValue
        else:
            if item:
                async with self.instance.Shops() as shops:
                    shops[shop]["Items"][item]["Type"] = _type.content.lower()
                    try:
                        del shops[shop]["Items"][item]["Messages"]
                        del shops[shop]["Items"][item]["Role"]
                        del shops[shop]["Items"][item]["cRarity"]
                        del shops[shop]["Items"][item]["aItemStats"]
                        del shops[shop]["Items"][item]["cashType"]
                        del shops[shop]["Items"][item]["cashValue"]
                    except KeyError:
                        pass
                return await self.ctx.send("Item type set to {}.".format(_type.content.lower()))
            return _type.content.lower(), None, None, None, None, None, None
        async with self.instance.Shops() as shops:
            shops[shop]["Items"][item]["Type"] = _type.content.lower()

    async def set_info(self, item=None, shop=None):
        await self.ctx.send("Specify the info text for this item.\n*Note* cannot be longer than 500 characters.")
        info = await self.ctx.bot.wait_for("message", timeout=40, check=Checks(self.ctx, length=500).length_under)
        if item:
            async with self.instance.Shops() as shops:
                shops[shop]["Items"][item]["Info"] = info.content
                return await self.ctx.send("Info now set to:\n{}".format(info.content))
        return info.content

    async def get_item(self):
        shops = await self.instance.Shops.all()

        await self.ctx.send("What shop is the item you would like to edit in?")
        shop = await self.ctx.bot.wait_for("message", timeout=25.0, check=Checks(self.ctx, custom=shops).content)

        items = shops[shop.content]["Items"]
        await self.ctx.send("What item would you like to edit?")
        item = await self.ctx.bot.wait_for("message", timeout=25.0, check=Checks(self.ctx, custom=items).content)

        return shop.content, item.content, shops[shop.content]["Items"][item.content]

    async def add(self, data, shop, item, new_allowed=False):
        async with self.instance.Shops() as shops:
            if shop not in shops:
                if new_allowed:
                    shops[shop] = {"Items": {item: data}, "Role": "@everyone"}
                    return log.info("Created the shop: {} and added {}.".format(shop, item))
                log.error("{} could not be added to {}, because it does not exist.".format(item, shop))
            elif item in shops[shop]["Items"]:
                log.error("{} was not added because that item already exists in {}.".format(item, shop))
            else:
                shops[shop]["Items"][item] = data
                log.info("{} added to {}.".format(item, shop))

    async def remove(self, shop, item, stock, amount):
        try:
            remainder = stock - amount
            async with self.instance.Shops() as shops:
                if remainder > 0:
                    shops[shop]["Items"][item]["Qty"] = remainder
                else:
                    del shops[shop]["Items"][item]
        except TypeError:
            pass
        return

class Parser:
    def __init__(self, ctx, instance, msg):
        self.ctx = ctx
        self.instance = instance
        self.msg = msg

    @staticmethod
    def basic_checks(idx, row):
        if len(row["Shop"]) > 25:
            log.warning("Row {} was not added because shop name was too long.".format(idx))
            return False
        elif len(row["Item"]) > 30:
            log.warning("Row {} was not added because item name was too long.".format(idx))
            return False
        elif not row["Cost"].isdigit() or int(row["Cost"]) < 0:
            log.warning("Row {} was not added because the cost was lower than 0.".format(idx))
            return False
        elif not row["Qty"].isdigit() or int(row["Qty"]) < 0:
            log.warning("Row {} was not added because the quantity was lower than 0.".format(idx))
            return False
        elif len(row["Info"]) > 500:
            log.warning("Row {} was not added because the info was too long.".format(idx))
            return False
        else:
            return True

    def type_checks(self, idx, row, messages):
        if row["Type"].lower() not in ("basic", "random", "auto", "role","achest","distributable","redeemable"):
            log.warning("Row {} was not added because of an invalid type.".format(idx))
            return False
        elif row["Type"].lower() == "role" and not row["Role"]:
            log.warning("Row {} was not added because the type is a role, but no role was set.".format(idx))
            return False
        elif (
            row["Type"].lower() == "role" and discord.utils.get(self.ctx.message.guild.roles, name=row["Role"]) is None
        ):
            log.warning(
                "Row {} was not added because the {} role does not exist on the server.".format(idx, row["Role"])
            )
            return False
        elif row["Type"].lower() == "auto" and int(row["Qty"]) == 0:
            log.warning("Row {} was not added because auto items cannot have an infinite quantity.".format(idx))
            return False
        elif row["Type"].lower() == "auto" and int(row["Qty"]) != len(messages):
            log.warning(
                "Row {} was not added because auto items must have an equal number of "
                "messages and quantity.".format(idx)
            )
            return False
        elif row["Type"].lower() == "auto" and any(len(x) > 2000 for x in messages):
            log.warning("Row {} was not added because one of the messages exceeds 2000 characters.".format(idx))
            return False
        elif row["Type"].lower() == "role":
            if discord.utils.get(self.ctx.message.guild.roles, name=row["Role"]) > self.ctx.author.top_role:
                log.warning(
                    "Row {} was not added because the {} role is higher than the "
                    "shopkeeper's highest role.".format(idx, row["Role"])
                )
                return False
            else:
                return True
        elif row["Type"].lower() == "achest" and not row["cRarity"]:
            log.warning("Row {} was not added because the type is an Adventure Chest, but no Rarity was set.".format(idx))
            return False
        elif row["Type"].lower() == "redeemable" or row["Type"].lower() == "distributable":
            return True
        else:
            return True

    async def parse_text_entry(self, text):
        keys = ("Shop", "Item", "Type", "Qty", "Cost", "Info", "Role", "Messages")
        raw_data = [
            [f.strip() for f in y] for y in [x.split(",") for x in text.strip("`").split("\n") if x] if 6 <= len(y) <= 8
        ]
        bulk = [{key: value for key, value in zip_longest(keys, x)} for x in raw_data]
        await self.parse_bulk(bulk)

    async def search_csv(self, file_path):
        try:
            with open(file_path,mode="rt",encoding="utf-8-sig") as f:
                reader = csv.DictReader(f, delimiter=",")
                await self.parse_bulk(reader)
        except FileNotFoundError:
            await self.msg.edit(content="The specified filename could not be found.")

    async def parse_bulk(self, reader):
        if not reader:
            return await self.msg.edit(content="Data was faulty. No data was added.")

        keys = ("Cost", "Qty", "Type", "Info", "Role", "Messages", "cRarity", "aItemStats", "cashType", "cashValue")
        for idx, row in enumerate(reader, 1):
            try:
                messages = [x.strip() for x in row["Messages"].split(",") if x]
            except AttributeError:
                messages = []

            if not self.basic_checks(idx, row):
                continue
            elif not self.type_checks(idx, row, messages):
                continue
            else:
                data = {
                    key: row.get(key, None)
                    if key not in ("Cost", "Qty", "Messages")
                    else int(row[key])
                    if key != "Messages"
                    else messages
                    for key in keys
                }
                if data["Qty"] == 0:
                    data["Qty"] = "--"
                if data["Type"].lower() == 'redeemable' or data["Type"].lower() == 'distributable':
                    if data["cashType"].lower() == 'nitro':
                        if int(data["cashValue"]) == 12:
                            data["cashValue"] == '1Y'
                            item_name = f"{prefix[data['Type'].lower()]} {itemCashType[data['cashType'].lower()]} - 1 Year"
                        else:
                            item_name = f"{prefix[data['Type'].lower()]} {itemCashType[data['cashType'].lower()]} - {data['cashValue']} Month(s)"
                    else:
                        item_name = f"{prefix[data['Type'].lower()]} {itemCashType[data['cashType'].lower()]} - USD {data['cashValue']}"
                else:
                    item_name = row["Item"]

                item_manager = ItemManager(self.ctx, self.instance)
                await item_manager.add(data, row["Shop"], item_name, new_allowed=True)
        await self.msg.edit(content="Bulk process finished. Please check your console for more information.")


class ExitProcess(Exception):
    pass
