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
    communico,
    gatech,
    library,
    localist,
    nature,
    puppetry,
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
    collect: Collector


def _source(slug: str, publisher: str, name: str, url: str, collect: Collector) -> CalendarSource:
    return CalendarSource(
        SourceInput(slug=slug, publisher=publisher, name=name, url=HttpUrl(url)), collect
    )


# When several calendars list the same event, the earliest one here supplies its details.
SOURCES = {
    source.config.slug: source
    for source in (
        _source(
            "gt-library",
            "Georgia Tech",
            "Georgia Tech Library",
            "https://library.gatech.edu/events-workshops",
            library.collect,
        ),
        _source(
            "gt-calendar",
            "Georgia Tech",
            "Georgia Tech Campus Calendar",
            "https://calendar.gatech.edu/rss-feeds",
            gatech.collect,
        ),
        _source(
            "emory-calendar",
            "Emory University",
            "Emory Events",
            "https://www.emory.edu/events",
            trumba.collect,
        ),
        _source(
            "gsu-calendar",
            "Georgia State University",
            "Georgia State Events",
            "https://calendar.gsu.edu",
            localist.collect,
        ),
        _source(
            "ksu-calendar",
            "Kennesaw State University",
            "Kennesaw State Events",
            "https://calendar.kennesaw.edu",
            partial(localist.collect, url="https://calendar.kennesaw.edu/calendar.ics"),
        ),
        _source(
            "agnes-scott-calendar",
            "Agnes Scott College",
            "Agnes Scott Events",
            "https://calendar.agnesscott.edu",
            partial(localist.collect, url="https://calendar.agnesscott.edu/calendar.ics"),
        ),
        _source(
            "dekalb-library",
            "DeKalb County Public Library",
            "DeKalb Library Events",
            "https://events.dekalblibrary.org/events",
            partial(communico.collect, library="dekalb"),
        ),
        _source(
            "gwinnett-library",
            "Gwinnett County Public Library",
            "Gwinnett Library Events",
            "https://gwinnettpl.libnet.info/events",
            partial(communico.collect, library="gwinnettpl"),
        ),
        _source(
            "trees-atlanta",
            "Trees Atlanta",
            "Trees Atlanta Events",
            nature.TREES_URL,
            nature.collect_trees,
        ),
        _source(
            "south-fork-conservancy",
            "South Fork Conservancy",
            "South Fork Conservancy Events",
            nature.SOUTH_FORK_URL,
            nature.collect_south_fork,
        ),
        _source(
            "piedmont-park",
            "Piedmont Park Conservancy",
            "Piedmont Park Events",
            "https://piedmontpark.org/calendar/",
            partial(
                tribe.collect, api_url="https://piedmontpark.org/wp-json/tribe/events/v1/events"
            ),
        ),
        _source(
            "atlanta-tech-village",
            "Atlanta Tech Village",
            "Atlanta Tech Village Events",
            "https://atlantatechvillage.com/events",
            tech_village.collect,
        ),
        _source(
            "travel-cobb",
            "Cobb Travel & Tourism",
            "Cobb Travel & Tourism Events",
            "https://travelcobb.org/cobb-county-events/",
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
            "dekalb-chamber",
            "DeKalb Chamber",
            "DeKalb Chamber Events",
            "https://business.dekalbchamber.org/events/calendar",
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
            puppetry.collect,
        ),
    )
}
