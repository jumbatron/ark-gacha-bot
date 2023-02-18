import time

from ark import Bed, Console, Dinosaur, Player, TribeLog, items
from discord import Embed  # type: ignore[import]

from ..station import Station, StationData

Y_TRAP_AVATAR = "https://static.wikia.nocookie.net/arksurvivalevolved_gamepedia/images/c/cb/Plant_Species_Y_Trap_%28Scorched_Earth%29.png/revision/latest?cb=20160901233007"
TRANSFER_PELLETS_BACK = 15


class YTrapStation(Station):
    """Represents a plant Y-Trap station, the most commonly executed station.
    Like all other stations, it follows the `Station` abstract base class.

    It keeps track of its current bed and increments it after completion, as
    mentioned in the gacha bot class, it might be better to split these things
    apart and create a ytrap station for each bed exclusively.

    Parameters:
    -----------
    station_data :class:`StationData`:
        A dataclass containing data about the station

    player :class:`Player`:
        The player controller handle responsible for movement

    tribelog :class:`Tribelog`:
        The tribelog object to check tribelogs when spawning

    """

    def __init__(
        self, station_data: StationData, player: Player, tribelog: TribeLog, bed: Bed
    ) -> None:
        self.station_data = station_data
        self.player = player
        self.tribelog = tribelog
        self.total_ytraps_deposited = 0
        self.last_refilled_pellets = time.time()
        self.gacha = Dinosaur("Gacha", "gacha")

    def complete(self) -> None:
        """Completes the Y-Trap station. Travels to the gacha station,
        empties the crop plots and fills the gacha.
        """
        # set times and tasks, travel to station
        self.spawn()
        start = time.time()
        try:
            # check if we need to initiate a refill lap
            if not self.refill_lap:
                self.check_refill_lap()

            # check if we need to refill the crop plots with pellets
            if self.refill_lap:
                self.take_pellets_from_gacha()

            # empty all the crop plots, load the gacha with the ytraps
            # changed it so it only does the crop plot stack precisely on refill laps
            self.player.do_precise_crop_plots(Y_TRAP, self.refill_lap, self.refill_lap)
            added_traps = self.load_gacha()

            # create the statistics for better data management
            stats = YTrapStatistics(
                time_taken=round(time.time() - start),
                refill_lap=self.refill_lap,
                profit={Y_TRAP: added_traps},
            )
            self.total_ytraps_deposited += added_traps
            return self.create_embed(stats), stats

        finally:
            # need to make sure we create our embed before we check this
            self.check_lap_finished()

    def set_gamma(self) -> None:
        """Sets gamma to 5"""
        console = Console()
        console.set_gamma("5")

    def check_refill_lap(self) -> None:
        """Check if we need to initiate a refill lap. True if a new lap has just
        started and more than 8 hours have passed since the last refill lap.
        """
        if (
            not self.current_bed
            and (time.time() - self.last_refilled_pellets) > 24 * 3600
        ):
            self.refill_lap = True

    def check_lap_finished(self) -> None:
        """Checks if we finished a lap, if we did and it was a refill lap
        set the 'last refilled' timer."""
        if self.current_bed < len(self.station_data.beds) - 1:
            self.current_bed += 1
            return

        # lap completed
        self.current_bed = 0
        if self.refill_lap:
            self.refill_lap = False
            self.last_refilled_pellets = time.time()
        self.current_lap += 1

    def take_pellets_from_gacha(self) -> None:
        """Takes the pellets from the gacha (on a refill lap only).

        Expects the gacha in a closed state, leaves the gacha in a closed state.
        Raises a `NoItemsAddedError` if no pellets could be taken from the gacha.
        """
        # check if we need to take pellets first
        self.gacha.inventory.open()
        self.gacha.inventory.search_for(PELLET)
        self.player.sleep(0.3)

        if not self.gacha.inventory.has_item(PELLET):
            # gacha has no pellets
            self.gacha.inventory.close()
            return

        # take the pellets and transfer some rows back (to make space)
        # for Y-Traps
        self.gacha.inventory.click_transfer_all()
        self.player.inventory.await_items_added()
        self.player.inventory.take_pellets(TRANSFER_PELLETS_BACK)
        self.gacha.inventory.close()

    def load_gacha(self) -> int:
        """Fills the gacha after emptying crop plots.

        TODO: Improve the ytrap counting because it currently counts the amount
        of stacks located in the inventory, but there is more than one page.

        Returns:
        -----------
        The amount of ytrap stacks deposited as `int`
        """

        # take all the pellets (to avoid losing out on traps because of cap)
        self.gacha.inventory.open()
        amount_of_traps = self.player.inventory.count_item(Y_TRAP) * 10
        ocr_amount = amount_of_traps > 400
        self.gacha.inventory.take_all_items(PELLET)

        # put traps back in, then add the remaining pellets
        self.player.inventory.transfer_all(self.gacha.inventory, Y_TRAP)
        if ocr_amount:
            amount_of_traps = self.player.inventory.get_amount_transferred(Y_TRAP, "rm")
        self.player.sleep(0.5)
        self.player.inventory.transfer_all(self.gacha.inventory)
        self.player.sleep(0.5)

        if self.player.inventory.has_item(Y_TRAP):
            self.player.inventory.popcorn(Y_TRAP)

        # drop rest of the pellets, close and return the amount of ytraps
        # deposited into the gacha
        self.player.inventory.click_drop_all()
        self.player.inventory.close()

        if 400 < amount_of_traps < 800:
            return amount_of_traps
        return 600 if ocr_amount else amount_of_traps

    def validate_stats(self, statistics: StationStatistics) -> str:
        """Checks on the amount of traps deposited given the current runtime.

        Parameters:
        ----------
        statistics :class:`StationStatistics`:
            The statistics object of the station to take the data from.

        Returns:
        ---------
        A string displaying if the time taken and the Y-Traps deposited
        are within a valid range.
        """
        result = ""
        # different expectations for first lap (more tasks, dead crop plots)
        if statistics.refill_lap:
            if statistics.time_taken > 150:
                return "Time taken was unusually long, even for the first lap!"
            return f"Station works as expected for the first lap!"

        # check trap amount, below 400 is not normal
        if statistics.profit[Y_TRAP] < 40:
            result += f"The amount of Y-Traps deposited is too low.\n"

        # check time taken for station
        if statistics.time_taken > 100:
            result += f"The time taken was unsually long!"

        return result if result else "Station works as expected."

    def create_embed(self, statistics: StationStatistics) -> Embed:
        """Creates a `discord.Embed` from the stations statistics.

        The embed contains info about what station was finished, if the time
        was within a normal range, how long it took and how many Y-Traps
        were deposited into the gacha.

        Parameters:
        ----------
        statistics :class:`StationStatistics`:
            The statistics object of the station to take the data from.

        Returns:
        ---------
        A formatted `discord.Embed` displaying the station statistics
        """
        embed = Embed(
            type="rich",
            title=f"Finished gacha station {self.station_data.beds[self.current_bed].name}!",
            description=(self.validate_stats(statistics)),
            color=0xFC97E8,
        )

        embed.add_field(name="Time taken:ㅤㅤㅤ", value=f"{statistics.time_taken} seconds")
        embed.add_field(name="Y-Traps deposited:", value=f"{statistics.profit[Y_TRAP]}")

        embed.set_thumbnail(url=Y_TRAP_AVATAR)
        embed.set_footer(text="Ling Ling on top!")
        return embed
