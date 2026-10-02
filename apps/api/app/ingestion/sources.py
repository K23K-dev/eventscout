"""Verified calendar configurations and their collection adapters."""

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Protocol

import httpx
from pydantic import HttpUrl

from app.ingestion import (
    carbonhouse,
    chambermaster,
    civic,
    communico,
    culture,
    gatech,
    icalendar_feed,
    library,
    localist,
    nature,
    puppetry,
    sports,
    tag,
    tech_village,
    tribe,
    trumba,
)
from app.ingestion.records import SourceCollection
from app.storage.models import SourceInput


class Collector(Protocol):
    def __call__(
        self, client: httpx.AsyncClient, *, window_start: datetime, window_end: datetime
    ) -> Awaitable[SourceCollection]: ...


@dataclass(frozen=True)
class CalendarSource:
    config: SourceInput
    priority: int
    collect: Collector


def _source(
    slug: str, publisher: str, name: str, url: str, priority: int, collect: Collector
) -> CalendarSource:
    return CalendarSource(
        SourceInput(slug=slug, publisher=publisher, name=name, url=HttpUrl(url)),
        priority,
        collect,
    )


SOURCES = {
    source.config.slug: source
    for source in (
        _source(
            "gt-calendar",
            "Georgia Tech",
            "Georgia Tech Campus Calendar",
            "https://calendar.gatech.edu/rss-feeds",
            1,
            gatech.collect,
        ),
        _source(
            "gt-library",
            "Georgia Tech",
            "Georgia Tech Library",
            "https://library.gatech.edu/events-workshops",
            0,
            library.collect,
        ),
        _source(
            "emory-calendar",
            "Emory University",
            "Emory Events",
            "https://www.emory.edu/events",
            2,
            trumba.collect,
        ),
        _source(
            "gsu-calendar",
            "Georgia State University",
            "Georgia State Events",
            "https://calendar.gsu.edu",
            3,
            localist.collect,
        ),
        _source(
            "ksu-calendar",
            "Kennesaw State University",
            "Kennesaw State Events",
            "https://calendar.kennesaw.edu",
            4,
            partial(localist.collect, url="https://calendar.kennesaw.edu/calendar.ics"),
        ),
        _source(
            "agnes-scott-calendar",
            "Agnes Scott College",
            "Agnes Scott Events",
            "https://calendar.agnesscott.edu",
            5,
            partial(localist.collect, url="https://calendar.agnesscott.edu/calendar.ics"),
        ),
        _source(
            "dekalb-library",
            "DeKalb County Public Library",
            "DeKalb Library Events",
            "https://events.dekalblibrary.org/events",
            6,
            partial(communico.collect, library="dekalb"),
        ),
        _source(
            "gwinnett-library",
            "Gwinnett County Public Library",
            "Gwinnett Library Events",
            "https://gwinnettpl.libnet.info/events",
            7,
            partial(communico.collect, library="gwinnettpl"),
        ),
        _source(
            "auc-connect",
            "Atlanta University Center Consortium",
            "AUC Connect",
            "https://connect.aucenter.edu/events",
            8,
            partial(
                icalendar_feed.collect,
                url="https://connect.aucenter.edu/ical/aucenter/ical_aucenter.ics",
                source_page="https://connect.aucenter.edu/events",
            ),
        ),
        _source(
            "atlanta-ham-radio",
            "Atlanta Ham Radio",
            "Atlanta Ham Radio Public Service Events",
            "https://atlantahamradio.org/pages/calendar-feed.html",
            9,
            partial(
                icalendar_feed.collect,
                url="https://atlantahamradio.org/events.ics",
                source_page="https://atlantahamradio.org/pages/calendar-feed.html",
                require_metro_venue=True,
            ),
        ),
        _source(
            "trees-atlanta",
            "Trees Atlanta",
            "Trees Atlanta Events",
            nature.TREES_URL,
            10,
            nature.collect_trees,
        ),
        _source(
            "south-fork-conservancy",
            "South Fork Conservancy",
            "South Fork Conservancy Events",
            nature.SOUTH_FORK_URL,
            11,
            nature.collect_south_fork,
        ),
        _source(
            "piedmont-park",
            "Piedmont Park Conservancy",
            "Piedmont Park Events",
            "https://piedmontpark.org/calendar/",
            12,
            partial(
                tribe.collect, api_url="https://piedmontpark.org/wp-json/tribe/events/v1/events"
            ),
        ),
        _source(
            "atlanta-tech-village",
            "Atlanta Tech Village",
            "Atlanta Tech Village Events",
            "https://atlantatechvillage.com/events",
            14,
            tech_village.collect,
        ),
        _source(
            "the-earl",
            "THE EARL",
            "THE EARL Live Music",
            "https://badearl.com/",
            16,
            culture.collect_earl,
        ),
        _source(
            "tag",
            "Technology Association of Georgia",
            "TAG Events",
            "https://members.tagonline.org/calendar",
            15,
            tag.collect,
        ),
        _source(
            "fernbank-museum",
            "Fernbank Museum",
            "Fernbank Museum Events",
            "https://www.fernbankmuseum.org/events/calendar-of-events/",
            17,
            culture.collect_fernbank,
        ),
        _source(
            "travel-cobb",
            "Cobb Travel & Tourism",
            "Cobb Travel & Tourism Events",
            "https://travelcobb.org/cobb-county-events/",
            18,
            partial(
                tribe.collect,
                api_url="https://travelcobb.org/wp-json/tribe/events/v1/events",
                crawl_delay=20,
            ),
        ),
        _source(
            "artsatl",
            "ArtsATL",
            "ArtsATL Events",
            "https://www.artsatl.org/calendar/",
            19,
            partial(
                tribe.collect,
                api_url="https://www.artsatl.org/wp-json/tribe/events/v1/events",
                excluded_cities=frozenset(
                    {
                        "athens",
                        "gainesville",
                        "columbus",
                        "lagrange",
                        "savannah",
                        "macon",
                        "rome",
                        "chattanooga",
                        "summerville",
                        "highlands",
                        "rabun gap",
                    }
                ),
            ),
        ),
        _source(
            "atlanta-falcons",
            "Atlanta Falcons",
            "Atlanta Falcons Home Games",
            sports.FALCONS_URL,
            20,
            sports.collect_falcons,
        ),
        _source(
            "norcross",
            "City of Norcross",
            "Norcross Events",
            "https://www.norcrossga.net/calendar.aspx?CID=22",
            21,
            partial(civic.collect, city="norcross"),
        ),
        _source(
            "lilburn",
            "City of Lilburn",
            "Lilburn Events",
            "https://www.cityoflilburn.com/calendar.aspx?CID=25",
            22,
            partial(civic.collect, city="lilburn"),
        ),
        _source(
            "lawrenceville",
            "City of Lawrenceville",
            "Lawrenceville Events",
            "https://www.lawrencevillega.org/calendar.aspx?CID=22",
            23,
            partial(civic.collect, city="lawrenceville"),
        ),
        _source(
            "dekalb-chamber",
            "DeKalb Chamber",
            "DeKalb Chamber Events",
            "https://business.dekalbchamber.org/events/calendar",
            24,
            partial(
                chambermaster.collect,
                calendar_url="https://business.dekalbchamber.org/events/calendar",
                publisher="DeKalb Chamber",
            ),
        ),
        _source(
            "brookhaven-chamber",
            "Brookhaven Chamber",
            "Brookhaven Chamber Events",
            "https://biz.brookhavencommerce.org/events/calendar",
            25,
            partial(
                chambermaster.collect,
                calendar_url="https://biz.brookhavencommerce.org/events/calendar",
                publisher="Brookhaven Chamber",
            ),
        ),
        _source(
            "greater-perimeter-chamber",
            "Greater Perimeter Chamber",
            "Greater Perimeter Chamber Events",
            "https://business.greaterperimeterchamber.com/events/calendar",
            26,
            partial(
                chambermaster.collect,
                calendar_url="https://business.greaterperimeterchamber.com/events/calendar",
                publisher="Greater Perimeter Chamber",
            ),
        ),
        _source(
            "atlanta-symphony",
            "Atlanta Symphony Orchestra",
            "Atlanta Symphony Orchestra Events",
            "https://www.aso.org/events",
            27,
            partial(
                carbonhouse.collect,
                calendar_url="https://www.aso.org/events",
                default_venue="Atlanta Symphony Hall",
            ),
        ),
        _source(
            "fox-theatre",
            "Fox Theatre",
            "Fox Theatre Events",
            "https://www.foxtheatre.org/events",
            28,
            partial(
                carbonhouse.collect,
                calendar_url="https://www.foxtheatre.org/events",
                default_venue="Fox Theatre, 660 Peachtree Street NE, Atlanta, GA",
            ),
        ),
        _source(
            "state-farm-arena",
            "State Farm Arena",
            "State Farm Arena Events",
            "https://www.statefarmarena.com/events",
            29,
            partial(
                carbonhouse.collect,
                calendar_url="https://www.statefarmarena.com/events",
                default_venue="State Farm Arena, 1 State Farm Drive, Atlanta, GA",
            ),
        ),
        _source(
            "center-for-puppetry-arts",
            "Center for Puppetry Arts",
            "Center for Puppetry Arts Performances",
            puppetry.CALENDAR_URL,
            30,
            puppetry.collect,
        ),
    )
}
