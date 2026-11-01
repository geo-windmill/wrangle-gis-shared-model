# Data sources and terms

**This repository contains no agency data.** `wrangle fetch` downloads it from each
agency's public ArcGIS REST service onto your machine. The code license does not
cover that data. Each source stays under its publisher's own terms, summarised below
from the terms attached to each service (checked October 2026). Several publishers
ask that you **not redistribute** their data. Please respect that: get it from them,
not from a copy.

None of this data is suitable for legal, engineering, design, or survey use. Every
publisher below distributes it "as is", with no warranty of accuracy.

## Roads — Sacramento region

| Id | Publisher | Terms stated by the publisher | Redistribution | Credit |
|---|---|---|---|---|
| R1 | Sacramento County | Creative Commons public domain dedication (CC0) | Allowed | County of Sacramento and Regional Partners |
| R2 | Yolo County | Accuracy disclaimer only; no license stated | Not stated | Yolo County GIS Cooperative |
| R3 | City of West Sacramento | City GIS disclaimer, "as is" | **Not allowed** — refer users to the City | City of West Sacramento |
| R4 | City of Elk Grove | No license on this layer; city disclaimer on its GIS data | Not stated | Elk Grove GIS |
| R5 | Caltrans | Provided for informational purposes, "as is" | Not stated | California Department of Transportation (Caltrans) |

## Fire hydrants — California

| Id | Publisher | Terms stated by the publisher | Redistribution | Credit |
|---|---|---|---|---|
| H1 | City of West Sacramento | City GIS disclaimer, "as is" | **Not allowed** — refer users to the City | City of West Sacramento |
| H2 | El Dorado County | County GIS Data Disclaimer | **Not without the originator's agreement** | County of El Dorado Surveyor Department GIS Division |
| H3 | City of Los Angeles | No terms attached to this service | Not stated | City of Los Angeles |

## Area-of-interest outlines (committed)

`data/boundaries/*.geojson` are derived from the U.S. Census Bureau's TIGERweb service:
a US government work, in the public domain. Census asks for acknowledgement:
*Source: U.S. Census Bureau, TIGERweb.*

## Basemap

The app's basemap is [OpenFreeMap](https://openfreemap.org), drawing
© [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors and
© OpenMapTiles. Every source's credit line is shown on the map while it is visible.
`wrangle cache basemap` keeps a copy of its tiles on your machine under `cache/basemap/`,
for drawing the map offline; like the agency data, that copy is never committed.
