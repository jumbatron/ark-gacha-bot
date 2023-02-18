import time
from dataclasses import dataclass
from datetime import datetime

from discord import Embed  # type: ignore[import]

from ark.entities.player import Player
from ark.entities.stryder import Stryder  # type: ignore[import]
from ark.exceptions import DediNotInRangeError
from ark.items import (ASSAULT_RIFLE, BEHEMOTH_GATE, BEHEMOTH_GATEWAY,
                       BLACK_PEARL, DUST, FAB, FLINT, FUNGAL_WOOD,
                       GACHA_CRYSTAL, METAL_GATE, METAL_GATEWAY, MINER_HELMET,
                       PUMPGUN, RIOT, STONE, TREE_PLATFORM, Item)
from ark.structures import TekDedicatedStorage, structure
from ark.structures.structure import Structure
from ark.tribelog import TribeLog
from bot.stations.arb.arb_station import ARBStation
from bot.stations.grinding.grinding_station import GrindingStation
from bot.stations.station import Station, StationData, StationStatistics
from bot.stations.ytrap.ytrap_station import YTrapStation

CRYSTAL_AVATAR = "https://static.wikia.nocookie.net/arksurvivalevolved_gamepedia/images/c/c3/Gacha_Crystal_%28Extinction%29.png/revision/latest?cb=20181108100408"
DUST_AVATAR = "https://static.wikia.nocookie.net/arksurvivalevolved_gamepedia/images/b/b1/Element_Dust.png/revision/latest/scale-to-width-down/228?cb=20181107161643"
DROP_ITEMS = ["primitive", "ramshackle"]

@dataclass
class CrystalStatistics:
    """Represents the stations statistics as dataclass.
    Follows the `StationStatistics` protocol.
    """

    time_taken: int
    refill_lap: bool
    profit: dict[Item, int]


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

    def __init__(
        self,
        station_data: StationData,
        player: Player,
        tribelog: TribeLog,
        grinding_station: GrindingStation,
        ytrap_station: YTrapStation,
        arb_station: ARBStation,
        stryder_depositing: bool,
    ) -> None:

        self.station_data = station_data
        self.tribelog = tribelog
        self.player = player
        self.grinding_station = grinding_station
        self.ytrap_station = ytrap_station
        self.arb_station = arb_station

        self.current_bed = 0
        self._stryder_depositing = stryder_depositing
        self._first_pickup = True
        self._total_pickups = 0
        self._total_dust_made = 0
        self._total_pearls_made = 0
        self._total_flint_made = 0
        self._total_stone_made = 0
        self._total_wood_made = 0
        self.dedi = TekDedicatedStorage()
        self.stryder = Stryder()

    def is_ready(self) -> bool:
        """Checks whether the station is ready or not"""
        if self.ytrap_station.total_ytraps_deposited < 2000:
            return False
        return super().is_ready()

    def complete(self) -> tuple[Embed, CrystalStatistics]:
        """Completes the crystal collection station of the given bed.

        Travels to the crystal station, picks, opens and deposits crystals and
        puts away the items into the vault as configured by the user.

        Keeps track of the amounts it has deposited into dedis and returns them.
        """
        try:
            self.spawn()
            start = time.time()

            # open the crystals and deposit the items into dedis
            self.pick_crystals()
            self.open_crystals()

            if not self._stryder_depositing:
                resources_deposited = self.deposit_dedis()
                resources_deposited[DUST] = self.validate_dust_amount(
                    resources_deposited[DUST]
                )
            else:
                resources_deposited = self.deposit_into_stryder()
                self.arb_station.add_wood(resources_deposited[FUNGAL_WOOD])

            # put items into vault
            vault_full = self.deposit_items()
            if vault_full:
                self.grinding_station.ready = True

            stats = CrystalStatistics(
                time_taken=round(time.time() - start),
                refill_lap=False,
                profit=resources_deposited,
            )

            # increase the counters
            self._total_pickups += 1
            self._total_dust_made += resources_deposited[DUST]
            self._total_pearls_made += resources_deposited[BLACK_PEARL]
            self._total_flint_made += resources_deposited[FLINT]
            self._total_stone_made += resources_deposited[STONE]
            self._total_wood_made += resources_deposited[FUNGAL_WOOD]

            return self.create_embed(stats), stats

        finally:
            self.station_data.last_completed = datetime.now()

    def deposit_into_stryder(self) -> dict[Item, int]:
        self.player.turn_y_by(-50)
        self.player.sleep(0.5)
        profits: dict[Item, int] = {}
        self.stryder.inventory.open()

        for item in [DUST, FLINT, STONE, FUNGAL_WOOD, BLACK_PEARL]:
            self.player.inventory.search_for(item)
            self.player.sleep(0.3)
            stacks = self.player.inventory.count_item(item)
            profits[item] = max(int((stacks * item.stack_size) - (0.5 * item.stack_size)), 0)
            self.player.inventory.click_transfer_all()
        self.stryder.inventory.close()

        self.stryder.sort_items_to_nearby_dedis()
        self.stryder.sleep(1)

        self.stryder.inventory.open()
        self.stryder.inventory.click_drop_all()
        self.stryder.inventory.close()
        return profits

    def walk_into_back(self) -> None:
        """Walks into the back of the collection point, attempts to pick
        up a crystal to determine if it has reached the back without lagging.

        Tries again if it does not get a crystal added.
        """
        self.player.walk("s", duration=3)
        self.player.pick_up()

        # wait for the crytal to be picked up
        if self.player.await_item_added():
            return

        # did not pick a crystal (timer popped?) Walk further
        self.player.sleep(3)
        self.player.walk("s", duration=3)

    def walk_forward_spam_f(self) -> None:
        """Slowly walks foward spaming the pick-up key to pick all the
        crystals while being angled slighty downwards.
        """
        for _ in range(9):
            self.player.pick_all()
            self.player.sleep(0.1)
            self.player.walk("w", duration=0.2)

        self.player.walk("w", 2)

    def walk_to_dedi(self) -> None:
        """Walks forward to dedi with various lag protection

        Being at the dedi is determined when we can see the deposit text.
        We then attempt to open the dedi to ensure its not lagging, if it doesnt
        open, the action wheel logic is used to determine if its lag or a bad angle.

        Raises:
        ---------
        `DediNotInRangeError` when the dedi cannot be accessed.
        """
        # look up further (so the deposit text 100% appears)
        self.player.turn_y_by(-70)

        # try to access the dedi, rewalk if its not possible
        count = 0
        while not self.dedi.inventory.can_be_opened():
            while not self.dedi.can_deposit():
                self.player.walk("w", 1)
                count += 1
                if count > 30:
                    raise DediNotInRangeError
            self.player.walk("w", 1)
        self.dedi.inventory.close()

    def pick_crystals(self) -> None:
        """Picks up the crystals in the collection point.

        Walks all the way into the back, slowly picks the crystals
        and walks back to the dedi with lag protection.
        """
        # walk back, crouch and look down
        self.walk_into_back()
        self.player.crouch()
        self.player.turn_y_by(80)

        self.walk_forward_spam_f()
        self.walk_to_dedi()

    def open_crystals(self) -> None:
        """Opens the crystals at the dedis, counting each iteration of the hotbar
        until there are no more crystals in our player inventory.

        Parameters:
        -----------
        first_time :class:`bool`:
            Whether the bot has opened crystals before, this will decide if it will
            put crystals into the hotbar or not.
        """
        # open inv and search for gacha crystals, click first crystal
        self.player.inventory.open()
        self.player.inventory.search_for(GACHA_CRYSTAL)
        self.player.inventory.click_at(163, 281)

        # put crystals into hotbar always on the first run
        if self._first_pickup:
            self.player.set_hotbar()
            self._first_pickup = False

        # open until no crystals left in inventory
        while self.player.inventory.has_item(GACHA_CRYSTAL):
            self.player.spam_hotbar()
            self.player.press("e")

        # go over the hotbar 5 more times to ensure no crystals left behind
        for _ in range(5):
            self.player.spam_hotbar()
        self.player.inventory.close()
        self.player.sleep(3)

    def deposit_dedis(self) -> dict[Item, int]:
        """Deposits all the dust / black pearls into the dedis.
        OCRs the amount amount deposited.

        Returns:
        -----------
        A dictionary containing the amounts of items deposited for dust and pearls
        """
        gains = {DUST: 0, BLACK_PEARL: 0, FLINT: 0, STONE: 0, FUNGAL_WOOD: 0}
        turns = {
            40: self.player.turn_x_by,
            -50: self.player.turn_y_by,
            -80: self.player.turn_x_by,
            50: self.player.turn_y_by,
        }
        # go through each turn depositing into dedi
        for val, func in turns.items():
            func(val)

            item_deposited = self.dedi.attempt_deposit([DUST, BLACK_PEARL])
            if not item_deposited:
                continue
            item, amount = item_deposited
            gains[item] += amount

        # return to original position
        self.player.turn_x_by(40)
        return gains

    def need_to_access_top_vault(self) -> bool:
        return any(
            self.player.inventory.has_item(item)
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
        vault = Structure("Vault", "vault", "templates/vault_capped.png")
        vault_full = False

        # put the grinding items in the left vault
        self.player.sleep(0.3)
        self.player.turn_90_degrees("left")
        self.player.sleep(1)
        vault.inventory.open()

        if not vault.inventory.is_full():
            # drop the shitty quality ones
            for drop_item in DROP_ITEMS:
                self.player.inventory.drop_all_items(drop_item)
                self.player.sleep(0.3)

            # transfer all the grinding items
            for keep_item in ["riot", "rifle", "miner", "pistol", "pump"]:
                self.player.inventory.transfer_all(vault.inventory, keep_item)
                self.player.sleep(0.2)
            vault_full = vault.inventory.is_full()

        else:
            vault_full = True

        if not self.need_to_access_top_vault() or self._stryder_depositing:
            vault.inventory.close()
            return vault_full

        # turn to the upper vault
        vault.inventory.close()
        self.player.turn_90_degrees("right")
        self.player.look_up_hard()
        vault.inventory.open()

        if vault.inventory.is_full():
            self.player.inventory.click_drop_all()
            vault.inventory.close()
            return vault_full

        # put structure stuff in it
        for item in [METAL_GATE, TREE_PLATFORM]:
            self.player.inventory.transfer_all(vault.inventory, item)
            self.player.sleep(0.2)
        self.player.inventory.click_drop_all()
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

    def create_embed(self, statistics: StationStatistics) -> Embed:

        dust = f"{statistics.profit[DUST]:_}".replace("_", " ")
        pearls = f"{statistics.profit[BLACK_PEARL]:_}".replace("_", " ")
        crystals = round(statistics.profit[DUST] / 150)

        embed = Embed(
            type="rich",
            title=f"Collected crystals at {self.station_data.beds[0].name}!",
            color=0x07F2EE,
        )
        embed.add_field(name="Time taken:ㅤㅤㅤ", value=f"{statistics.time_taken} seconds")
        embed.add_field(name="Crystals opened:", value=f"~{crystals} crystals")

        embed.add_field(name="\u200b", value="\u200b")
        embed.add_field(name="Dust made:", value=f"{dust}")
        embed.add_field(name="Black Pearls made:", value=f"{pearls}")
        embed.add_field(name="\u200b", value="\u200b")

        embed.set_thumbnail(url=CRYSTAL_AVATAR)
        embed.set_footer(text="Ling Ling on top!")

        return embed
