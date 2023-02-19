import time
from datetime import datetime

from ark import (Bed, Player, Structure, Stryder, TekDedicatedStorage,
                 TribeLog, _tools)
from ark.exceptions import DediNotInRangeError
from ark.items import *
from discord import Embed  # type: ignore[import]

from ...exceptions import NoCrystalAddedError
from ...webhooks import InfoWebhook
from .._station import Station
# from ..arb import ARBStation
# from ..grinding import GrindingStation
from ..ytrap import YTrapStation


class CrystalStation(Station):
    """Crystal Station handle.
    ------------------------------
    Follows the `Station` abstract base class and uses its default implementations.
    Has two different use modes, regular dedi depositing and stryder depositing
    where stryder depositing is required to run the ARB station and makes checking
    amounts gained alot more accurate.

    Additionally, the crystal station is responsible for setting other stations ready.

    Parameters:
    -----------
    station_data :class:`StationData`:
        A dataclass containing data about the station

    player :class:`Player`:
        The player controller handle responsible for movement

    tribelog :class:`Tribelog`:
        The tribelog object to check tribelogs when spawning

    grinding_station :class:`GrindingStation`:
        The grinding station object to set ready when the vault is capped

    arb_station :class:`ARBStation`:
        The arb station object to add the wood to
    """

    CRYSTAL_AVATAR = "https://static.wikia.nocookie.net/arksurvivalevolved_gamepedia/images/c/c3/Gacha_Crystal_%28Extinction%29.png/revision/latest?cb=20181108100408"
    DUST_AVATAR = "https://static.wikia.nocookie.net/arksurvivalevolved_gamepedia/images/b/b1/Element_Dust.png/revision/latest/scale-to-width-down/228?cb=20181107161643"
    DROP_ITEMS = ["primitive", "ramshackle"]

    _ITEMS = [
        ASSAULT_RIFLE,
        BEHEMOTH_GATE,
        BEHEMOTH_GATEWAY,
        BLACK_PEARL,
        DUST,
        FAB,
        FLINT,
        FUNGAL_WOOD,
        GACHA_CRYSTAL,
        METAL_GATE,
        METAL_GATEWAY,
        MINER_HELMET,
        PUMPGUN,
        RIOT,
        STONE,
        TREE_PLATFORM,
    ]

    def __init__(
        self,
        name: str,
        player: Player,
        tribelog: TribeLog,
        interval: int,
        info_webhook: InfoWebhook,
        grinding_station,
        arb_station,
        *,
        stryder_depositing: bool,
        drop_quality: list[str],
        keep_items: list[str],
    ) -> None:

        self._name = name
        self._player = player
        self._tribelog = tribelog
        self._webhook = info_webhook
        self.interval = interval
        self.bed = Bed(name)
        self.dedi = TekDedicatedStorage()
        self.stryder = Stryder()

        self._grinding_station = grinding_station
        self._arb_station = arb_station
        self._stryder_depositing = stryder_depositing

        self._keep_items: list[Item | str] = []
        self._drop_quality = drop_quality
        self._first_pickup = True

        for item in self._ITEMS:
            if item.name in keep_items:
                self._keep_items.append(item)
                keep_items.remove(item.name)
        self._keep_items.extend(keep_items)

        self._total_pickups = 0
        self._resources_made: dict[Item, int] = {}
        self.last_completed = datetime.now()

    def complete(self) -> None:
        """Completes the crystal collection station.

        Travels to the crystal station, picks, opens and deposits crystals and
        puts away the items into the vault as configured by the user.

        Keeps track of the amounts it has deposited into dedis and returns them.
        """
        try:
            self.spawn()
            start = time.time()

            # open the crystals and deposit the items into dedis
            self._pick_crystals()
            self._walk_to_dedi()
            self._open_crystals()

            if self._stryder_depositing:
                resources_deposited = self.deposit_into_stryder()
                # self._arb_station.add_wood(resources_deposited[FUNGAL_WOOD])
            else:
                resources_deposited = self.deposit_dedis()

            # put items into vault
            vault_full = self.deposit_items()
            if vault_full and 0:
                self._grinding_station.ready = True

            # increase the counters
            self._total_pickups += 1
            for item, amount in resources_deposited.items():
                got = self._resources_made.get(item, 0)
                self._resources_made[item] = got + amount

            embed = self.create_embed(resources_deposited, round(time.time() - start))
            self._webhook.send_embed(embed)

        finally:
            self.last_completed = datetime.now()

    def deposit_into_stryder(self) -> dict[Item, int]:
        self._player.turn_y_by(-50, delay=0.5)
        profits: dict[Item, int] = {}

        self.stryder.inventory.open()
        self.stryder.inventory.drop_all()

        for item in [DUST, FLINT, STONE, FUNGAL_WOOD, BLACK_PEARL]:
            self._player.inventory.search(item)
            self._player.sleep(0.3)
            stacks = self._player.inventory.count(item)
            profits[item] = max(
                int((stacks * item.stack_size) - (0.5 * item.stack_size)), 0
            )
            self._player.inventory.transfer_all()

        self.stryder.inventory.close()

        self.stryder.sort_items_to_nearby_dedis()
        self.stryder.sleep(1)
        return profits

    def _pick_crystals(self) -> None:
        """Picks up the crystals in the collection point.

        Walks all the way into the back, slowly picks the crystals
        and walks back to the dedi with lag protection.
        """
        # walk back, crouch and look down
        self._walk_into_back()
        self._player.crouch()
        self._player.turn_y_by(80)

        self._walk_forward_spam_f()

    def _walk_into_back(self) -> None:
        """Walks into the back of the collection point, attempts to pick
        up a crystal to determine if it has reached the back without lagging.

        Tries again if it does not get a crystal added.
        """
        self._player.walk("s", duration=3)
        self._player.pick_up()

        # wait for the crytal to be picked up
        if _tools.await_event(self._player.received_item, max_duration=3):
            return

        # did not pick a crystal, if its first collection we assume there is none
        if self._first_pickup:
            raise NoCrystalAddedError

        self._player.sleep(3)
        self._player.walk("s", duration=3)

    def _walk_forward_spam_f(self) -> None:
        """Slowly walks foward spaming the pick-up key to pick all the
        crystals while being angled slighty downwards.
        """
        for _ in range(9):
            self._player.pick_all()
            self._player.sleep(0.1)
            self._player.walk("w", duration=0.2)

        self._player.walk("w", 2)

    def _walk_to_dedi(self) -> None:
        """Walks forward to dedi with various lag protection

        Being at the dedi is determined when we can see the deposit text.
        We then attempt to open the dedi to ensure its not lagging, if it doesnt
        open, the action wheel logic is used to determine if its lag or a bad angle.

        Raises:
        ---------
        `DediNotInRangeError` when the dedi cannot be accessed.
        """
        # look up further (so the deposit text 100% appears)
        self._player.turn_y_by(-70)

        # try to access the dedi, rewalk if its not possible
        count = 0
        while not self.dedi.can_be_opened():
            while not self.dedi.is_in_deposit_range():
                self._player.walk("w", 1)
                count += 1
                if count > 30:
                    raise DediNotInRangeError
            self._player.walk("w", 1)
        self.dedi.inventory.close()

    def _open_crystals(self) -> None:
        """Opens the crystals at the dedis, counting each iteration of the hotbar
        until there are no more crystals in our player inventory.

        Parameters:
        -----------
        first_time :class:`bool`:
            Whether the bot has opened crystals before, this will decide if it will
            put crystals into the hotbar or not.
        """
        # open inv and search for gacha crystals, click first crystal
        self._player.inventory.open()
        self._player.inventory.search(GACHA_CRYSTAL)
        self._player.inventory.select_slot(0)

        # put crystals into hotbar always on the first run
        if self._first_pickup:
            self._player.set_hotbar()
            self._first_pickup = False

        # open until no crystals left in inventory
        while self._player.inventory.has(GACHA_CRYSTAL):
            self._player.spam_hotbar()
            self._player.pick_up()

        # go over the hotbar 5 more times to ensure no crystals left behind
        for _ in range(5):
            self._player.spam_hotbar()
        self._player.inventory.close()
        self._player.sleep(3)

    def deposit_dedis(self) -> dict[Item, int]:
        """Deposits all the dust / black pearls into the dedis.
        OCRs the amount amount deposited.

        Returns:
        -----------
        A dictionary containing the amounts of items deposited for dust and pearls
        """
        gains = {DUST: 0, BLACK_PEARL: 0}
        turns = [
            lambda: self._player.turn_x_by(40, delay=0.2),
            lambda: self._player.turn_y_by(-50, delay=0.2),
            lambda: self._player.turn_x_by(-80, delay=0.2),
            lambda: self._player.turn_y_by(50, delay=0.2),
        ]

        # go through each turn depositing into dedi
        for turn in turns:
            turn()

            item_deposited = self.dedi.deposit([DUST, BLACK_PEARL], get_amount=True)
            if item_deposited is None:
                continue
            item, amount = item_deposited
            gains[item] += amount

        # return to original position
        self._player.turn_x_by(40, delay=0.2)
        return gains

    def need_to_access_top_vault(self) -> bool:
        return any(
            self._player.inventory.has(item)
            for item in [
                TREE_PLATFORM,
                BEHEMOTH_GATE,
                BEHEMOTH_GATEWAY,
                METAL_GATE,
                METAL_GATEWAY,
            ]
        )

    def deposit_items(self) -> bool:
        """Puts the gear items into the vaults.

        Returns:
        --------
        Whether the vault is full after or before depositing items.
        """
        vault = Structure(
            "Vault", "templates/vault.png", capacity="templates/vault_capped.png"
        )
        vault_full = False

        # put the grinding items in the left vault
        self._player.sleep(0.3)
        self._player.turn_90_degrees("left")
        self._player.sleep(1)
        vault.inventory.open()

        if not vault.inventory.is_full():
            self._player.inventory.drop_all(self._drop_quality)
            self._player.inventory.transfer_all(self._keep_items)

            vault_full = vault.inventory.is_full()
        else:
            vault_full = True

        if not self.need_to_access_top_vault() or self._stryder_depositing:
            vault.inventory.close()
            return vault_full

        # turn to the upper vault
        vault.inventory.close()
        self._player.turn_90_degrees("right")
        self._player.look_up_hard()
        vault.inventory.open()

        if vault.inventory.is_full():
            self._player.inventory.drop_all()
            vault.inventory.close()
            return vault_full

        self._player.inventory.transfer_all([METAL_GATE, TREE_PLATFORM])

        self._player.inventory.drop_all()
        vault.inventory.close()
        return vault_full

    def validate_dust_amount(self, amount: int) -> int:
        """Checks if the given amount of dust is valid compared to the usual average.

        Parameters:
        -----------
        amount :class:`int`:
            The amount of dust we think we got

        Returns:
        ----------
        The given amount if its within a valid range, else the average amount
        """
        return amount
        try:
            average_amount = round(self._total_dust_made / self._total_pickups)
        except ZeroDivisionError:
            # assume 6000 dust / minute, or 100 / second
            average_amount = round(100 * self.station_data.interval)

        if average_amount - 5000 < amount < average_amount + 10000:
            return amount
        return average_amount

    def create_embed(self, profit: dict[Item, int], time_taken: int) -> Embed:

        dust = f"{profit[DUST]:_}".replace("_", " ")
        pearls = f"{profit[BLACK_PEARL]:_}".replace("_", " ")
        crystals = round(profit[DUST] / 150)

        embed = Embed(
            type="rich",
            title=f"Collected crystals at '{self._name}'!",
            color=0x07F2EE,
        )
        embed.add_field(
            name="Time taken:ㅤㅤㅤ", value=f"{time_taken} seconds"
        )
        embed.add_field(name="Crystals opened:", value=f"~{crystals} crystals")

        embed.add_field(name="\u200b", value="\u200b")
        embed.add_field(name="Dust made:", value=f"{dust}")
        embed.add_field(name="Black Pearls made:", value=f"{pearls}")
        embed.add_field(name="\u200b", value="\u200b")

        embed.set_thumbnail(url=self.CRYSTAL_AVATAR)
        embed.set_footer(text="Ling Ling on top!")

        return embed
