"""Fixed places on the globe: where staple foods come from, the sea lanes and
ports they travel through, and the markets where they are priced.

These are long-standing, widely documented facts, kept short on purpose. The
live layers (news, earthquakes, storms) come from Nimble and public feeds.
"""
from __future__ import annotations

# (item it supplies, place, lat, lng, one plain sentence)
ORIGINS = [
    ("coffee", "Minas Gerais, Brazil", -18.5, -44.6, "Brazil grows about a third of the world's coffee, most of it here."),
    ("coffee", "Central Highlands, Vietnam", 12.7, 108.0, "Vietnam is the largest grower of robusta, the bean in instant coffee."),
    ("coffee", "Huila, Colombia", 2.5, -75.6, "Colombia is a leading grower of mild arabica coffee."),
    ("chocolate", "Côte d'Ivoire", 6.8, -5.3, "The world's largest cocoa grower. Chocolate prices start here."),
    ("chocolate", "Ghana", 6.7, -1.6, "The second-largest cocoa grower."),
    ("beef", "Texas Panhandle, United States", 35.2, -101.8, "One of the largest cattle-feeding regions in the country."),
    ("beef", "Nebraska, United States", 41.5, -99.8, "A major state for cattle feeding and beef packing."),
    ("eggs", "Iowa, United States", 42.0, -93.5, "The largest egg-producing state."),
    ("milk", "Wisconsin, United States", 44.5, -89.5, "A leading dairy state."),
    ("milk", "Central Valley, California", 36.5, -119.8, "California produces more milk than any other state."),
    ("orange juice", "Polk County, Florida", 27.95, -81.7, "The heart of Florida's orange groves."),
    ("orange juice", "São Paulo State, Brazil", -21.8, -48.2, "Brazil is the world's largest exporter of orange juice."),
    ("olive oil", "Jaén, Spain", 37.77, -3.79, "Spain is the largest olive oil producer, and most of it comes from this region."),
    ("bread", "Kansas, United States", 38.5, -98.4, "A leading wheat state. Wheat becomes flour, bread and pasta."),
    ("bread", "Odesa, Ukraine", 46.48, 30.73, "A major port for Black Sea grain exports."),
    ("bananas", "Guayas, Ecuador", -2.2, -79.9, "Ecuador is the world's largest banana exporter."),
    ("bananas", "Guatemala", 15.5, -90.3, "A leading supplier of bananas to the United States."),
    ("rice", "Arkansas, United States", 34.8, -91.5, "The largest rice-growing state."),
    ("rice", "Thailand", 15.0, 100.5, "A major rice exporter."),
    ("avocados", "Michoacán, Mexico", 19.4, -102.0, "Supplies most of the avocados eaten in the United States."),
    ("chicken", "Georgia, United States", 32.6, -83.4, "The largest chicken-producing state."),
    ("tomatoes", "Sinaloa, Mexico", 25.0, -107.5, "A major winter supplier of fresh tomatoes to the United States."),
    ("potatoes", "Idaho, United States", 43.6, -114.5, "The largest potato-growing state."),
    ("apples", "Washington State, United States", 47.4, -120.3, "Grows most of the country's apples."),
    ("animal feed", "Illinois, United States", 40.0, -89.2, "Corn and soybeans grown here feed livestock, so feed costs flow into meat, eggs and milk."),
    ("fuel", "Permian Basin, Texas", 31.9, -102.1, "The largest US oil field. Fuel costs are part of every delivery."),
]

# (name, lat, lng, one plain sentence)
ROUTES = [
    ("Panama Canal", 9.08, -79.68, "Links the Atlantic and Pacific. In dry years low water limits how many ships can cross."),
    ("Suez Canal", 30.6, 32.3, "The shortcut between Asia and Europe."),
    ("Strait of Hormuz", 26.6, 56.3, "About a fifth of the world's oil passes through this strait."),
    ("Strait of Malacca", 2.5, 101.3, "The main sea lane between the Indian and Pacific oceans."),
    ("Bab-el-Mandeb", 12.6, 43.3, "The southern gate of the Red Sea, on the Asia to Europe route."),
    ("Bosphorus", 41.1, 29.05, "Black Sea grain reaches the world through this strait."),
    ("Port of Los Angeles and Long Beach", 33.74, -118.26, "The busiest container port complex in the United States."),
    ("Port of New York and New Jersey", 40.67, -74.05, "The largest port on the US East Coast."),
    ("Port of South Louisiana", 30.05, -90.5, "Most US grain exports travel down the Mississippi and leave from here."),
    ("Port of Santos, Brazil", -23.95, -46.33, "Latin America's largest port. Coffee, sugar, soy and orange juice ship from here."),
    ("Port of Rotterdam", 51.95, 4.14, "Europe's largest port."),
    ("Port of Shanghai", 31.23, 121.5, "The world's busiest container port."),
    ("Port of Singapore", 1.26, 103.82, "A hub where cargo changes ships between Asia, Europe and the Americas."),
]

MARKETS = [
    ("New York Stock Exchange", 40.707, -74.011, "The largest stock exchange. Shares of big US companies change hands here."),
    ("CME Group, Chicago", 41.88, -87.63, "Where contracts for cattle, corn, wheat and interest rates are traded."),
    ("Intercontinental Exchange, Atlanta", 33.75, -84.39, "Runs the market where coffee, cocoa, sugar and orange juice contracts are traded."),
    ("Federal Reserve, Washington", 38.893, -77.045, "Sets US interest rates, which move the cost of loans and the price of stocks."),
    ("London", 51.515, -0.09, "Home to the London Stock Exchange and the London Metal Exchange."),
    ("European Central Bank, Frankfurt", 50.11, 8.68, "Sets interest rates for the euro area."),
    ("Tokyo Stock Exchange", 35.68, 139.77, "One of the world's largest stock exchanges."),
]


def atlas(staples: list[str]) -> dict:
    """Everything fixed that the globe draws, with the shopper's own staples marked."""
    def mine(item: str) -> bool:
        return any(item in s or s.rstrip("s") in item or item.rstrip("s") in s for s in staples if len(s) > 2)

    return {
        "origins": [{"item": i, "place": p, "lat": la, "lng": ln, "note": n, "yours": mine(i)} for i, p, la, ln, n in ORIGINS],
        "routes": [{"name": n, "lat": la, "lng": ln, "note": t} for n, la, ln, t in ROUTES],
        "markets": [{"name": n, "lat": la, "lng": ln, "note": t} for n, la, ln, t in MARKETS],
    }
