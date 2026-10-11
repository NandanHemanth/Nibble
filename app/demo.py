"""Sample data used when a key is missing, so the app can be shown end to end.

Everything returned from here is flagged `sample: True` and the page labels it
as sample data. Prices are illustrative, not live quotes. The explanations
describe well-known, widely reported drivers in general terms and carry no
citations; live runs replace them with Nimble's cited research.
"""
from __future__ import annotations

SAMPLE_LIST = ["large eggs 12 count", "whole milk 1 gallon", "ground coffee 12 oz", "ground beef 1 lb",
               "orange juice 52 fl oz", "extra virgin olive oil 16.9 fl oz", "bananas 1 lb", "white bread loaf"]

# item keyword -> {store: (name, price, qty, unit)}
_PRICES = {
    "egg": {"Walmart": ("Great Value Large White Eggs, 12 Count", 3.47, 12, "count"),
            "Amazon": ("Amazon Grocery Large White Eggs, 12 Count", 3.89, 12, "count"),
            "Target": ("Good & Gather Grade A Large Eggs, 12ct", 3.69, 12, "count")},
    "milk": {"Walmart": ("Great Value Whole Vitamin D Milk, 1 Gallon", 3.38, 1, "gal"),
             "Amazon": ("Amazon Grocery Whole Milk, 1 Gallon", 3.79, 1, "gal"),
             "Target": ("Good & Gather Whole Milk, 1 gal", 3.59, 1, "gal")},
    "coffee": {"Walmart": ("Folgers Classic Roast Ground Coffee, 11.3 oz", 6.48, 11.3, "oz"),
               "Amazon": ("Amazon Fresh Colombia Ground Coffee, 12 oz", 6.99, 12, "oz"),
               "Target": ("Good & Gather Medium Roast Ground Coffee, 12oz", 6.19, 12, "oz")},
    "beef": {"Walmart": ("80% Lean Ground Beef Chuck, 1 lb", 6.24, 1, "lb"),
             "Amazon": ("Amazon Grocery 80% Lean Ground Beef, 1 lb", 6.79, 1, "lb"),
             "Target": ("Good & Gather 80/20 Ground Beef, 1lb", 6.49, 1, "lb")},
    "orange": {"Walmart": ("Great Value 100% Orange Juice, 52 fl oz", 3.78, 52, "fl oz"),
               "Amazon": ("Simply Orange Pulp Free Juice, 52 fl oz", 4.69, 52, "fl oz"),
               "Target": ("Good & Gather 100% Orange Juice, 52 fl oz", 3.99, 52, "fl oz")},
    "olive": {"Walmart": ("Great Value Extra Virgin Olive Oil, 17 fl oz", 7.42, 17, "fl oz"),
              "Amazon": ("Amazon Grocery Extra Virgin Olive Oil, 16.9 fl oz", 7.19, 16.9, "fl oz"),
              "Target": ("Good & Gather Extra Virgin Olive Oil, 16.9 fl oz", 7.99, 16.9, "fl oz")},
    "banana": {"Walmart": ("Fresh Bananas, per lb", 0.54, 1, "lb"),
               "Amazon": ("Fresh Bananas, per lb", 0.59, 1, "lb"),
               "Target": ("Fresh Bananas, per lb", 0.62, 1, "lb")},
    "bread": {"Walmart": ("Great Value White Sandwich Bread, 20 oz", 1.42, 20, "oz"),
              "Amazon": ("Amazon Grocery White Bread, 20 oz", 1.69, 20, "oz"),
              "Target": ("Market Pantry White Sandwich Bread, 20oz", 1.59, 20, "oz")},
    "chocolate": {"Walmart": ("Hershey's Milk Chocolate Bar, 4.4 oz", 2.48, 4.4, "oz"),
                  "Amazon": ("Hershey's Milk Chocolate Bar, 4.4 oz", 2.79, 4.4, "oz"),
                  "Target": ("Hershey's Milk Chocolate Bar, 4.4oz", 2.59, 4.4, "oz")},
}


def offers_for(item: str) -> dict[str, list[dict]]:
    """Sample listings per store for one item, or {} when the item is unknown."""
    low = item.lower()
    if any(w in low for w in ("oat", "almond", "soy ", "plant", "vegan", "gluten")):
        return {}  # the samples are the plain staple only
    for key, stores in _PRICES.items():
        if key in low:
            return {
                store: [{"name": name, "price": price, "url": None, "image": None, "size_hint": f"{qty} {unit}",
                         "rating": None, "sponsored": False, "store": store, "source": "sample", "localized": False,
                         "pack_quantity": qty, "pack_unit": unit}]
                for store, (name, price, qty, unit) in stores.items()
            }
    return {}


_WHY = {
    "egg": {
        "headline": "Bird flu drives the swings in egg prices",
        "direction": "flat",
        "change_summary": "Egg prices swing sharply whenever outbreaks force farms to cull hens.",
        "drivers": [{"title": "Avian influenza outbreaks", "category": "disease", "location_name": "Iowa, United States",
                     "lat": 42.0, "lng": -93.5,
                     "explanation": "Highly pathogenic avian influenza has repeatedly hit commercial laying flocks in the Midwest. Infected flocks are culled, and replacing hens takes months, so supply drops faster than it recovers."},
                    {"title": "Feed and energy costs", "category": "energy", "location_name": "Nebraska, United States",
                     "lat": 41.5, "lng": -99.8,
                     "explanation": "Corn and soybean meal are most of a hen's diet. When grain or fuel costs rise, the cost of every dozen rises with them."}],
        "exposed": [{"name": "Cal-Maine Foods", "ticker": "CALM", "kind": "company", "relationship": "Largest US egg producer; its revenue follows wholesale egg prices."},
                    {"name": "Corn futures", "ticker": None, "kind": "commodity", "relationship": "Main feed input for laying hens."},
                    {"name": "Restaurant chains with breakfast menus", "ticker": None, "kind": "sector", "relationship": "Eggs are a direct menu cost."}],
        "outlook": "Prices tend to ease as flocks rebuild and spike again with new outbreaks.",
        "shopper_tip": "Compare the 18-count carton: the per-egg price is often lower.",
    },
    "coffee": {
        "headline": "Bad weather in Brazil and Vietnam squeezed the bean supply",
        "direction": "up",
        "change_summary": "Wholesale coffee prices rose steeply after poor harvests in the two largest producers.",
        "drivers": [{"title": "Drought and frost in Brazil", "category": "weather", "location_name": "Minas Gerais, Brazil",
                     "lat": -18.5, "lng": -44.6,
                     "explanation": "Brazil grows about a third of the world's coffee. Drought and frost in its main arabica region cut yields, and arabica trees take years to recover."},
                    {"title": "Dry seasons in Vietnam", "category": "weather", "location_name": "Central Highlands, Vietnam",
                     "lat": 12.7, "lng": 108.0,
                     "explanation": "Vietnam is the largest robusta grower. Dry conditions reduced the crop used in instant and blended coffee."},
                    {"title": "Import tariffs", "category": "trade_policy", "location_name": "Port of New Orleans, United States",
                     "lat": 29.95, "lng": -90.06,
                     "explanation": "The US imports nearly all of its coffee, so tariffs on producing countries pass through to shelf prices."}],
        "exposed": [{"name": "Arabica coffee futures", "ticker": None, "kind": "commodity", "relationship": "The benchmark roasters buy against."},
                    {"name": "J.M. Smucker (Folgers)", "ticker": "SJM", "kind": "company", "relationship": "Large US roaster; green coffee is a major input cost."},
                    {"name": "Starbucks", "ticker": "SBUX", "kind": "company", "relationship": "Buys arabica at scale and hedges part of its cost."}],
        "outlook": "Shelf prices lag wholesale prices by months in both directions.",
        "shopper_tip": "Store-brand whole bean is usually the cheapest per ounce.",
    },
    "beef": {
        "headline": "The US cattle herd is the smallest in decades",
        "direction": "up",
        "change_summary": "Beef prices climbed as years of drought shrank the herd.",
        "drivers": [{"title": "Drought across the Plains", "category": "weather", "location_name": "Texas Panhandle, United States",
                     "lat": 35.2, "lng": -101.8,
                     "explanation": "Dry pastures and expensive hay pushed ranchers to sell cows early. Rebuilding a herd takes years because each cow raises one calf a year."},
                    {"title": "Limits on cattle imports", "category": "trade_policy", "location_name": "Chihuahua, Mexico",
                     "lat": 28.6, "lng": -106.1,
                     "explanation": "Restrictions on live cattle imports tied to a livestock pest reduced the supply of feeder cattle."}],
        "exposed": [{"name": "Tyson Foods", "ticker": "TSN", "kind": "company", "relationship": "Beef packer; pays more for cattle when supply is tight."},
                    {"name": "Live cattle futures", "ticker": None, "kind": "commodity", "relationship": "Benchmark for fed cattle prices."},
                    {"name": "Burger chains", "ticker": None, "kind": "sector", "relationship": "Ground beef is their largest food cost."}],
        "outlook": "Herd rebuilding is slow, so supply stays tight for some time.",
        "shopper_tip": "Family packs and 73/27 blends cost less per pound.",
    },
    "orange": {
        "headline": "Florida's groves have been shrinking for years",
        "direction": "up",
        "change_summary": "Orange juice costs more as the crop behind it keeps getting smaller.",
        "drivers": [{"title": "Citrus greening disease", "category": "disease", "location_name": "Polk County, Florida, United States",
                     "lat": 27.95, "lng": -81.7,
                     "explanation": "A bacterial disease spread by an insect has cut Florida's orange output to a fraction of its past level."},
                    {"title": "Hurricanes", "category": "weather", "location_name": "Southwest Florida, United States",
                     "lat": 26.6, "lng": -81.9,
                     "explanation": "Storms have stripped fruit and damaged trees in the main growing counties."},
                    {"title": "Weather in Brazil", "category": "weather", "location_name": "São Paulo State, Brazil",
                     "lat": -21.8, "lng": -48.2,
                     "explanation": "Brazil supplies most of the world's orange juice, so heat and drought there move prices everywhere."}],
        "exposed": [{"name": "Frozen concentrated orange juice futures", "ticker": None, "kind": "commodity", "relationship": "The traded benchmark for juice."},
                    {"name": "Tropicana Brands Group", "ticker": None, "kind": "company", "relationship": "Privately held juice maker; buys oranges and concentrate at these prices."}],
        "outlook": "New trees take years to bear fruit, so supply recovers slowly.",
        "shopper_tip": "Frozen concentrate is usually the cheapest per glass.",
    },
    "olive": {
        "headline": "Drought in Spain cut the olive harvest",
        "direction": "up",
        "change_summary": "Olive oil prices jumped after back-to-back poor harvests in southern Europe.",
        "drivers": [{"title": "Heat and drought in Andalusia", "category": "weather", "location_name": "Jaén, Spain",
                     "lat": 37.77, "lng": -3.79,
                     "explanation": "Spain is the world's largest olive oil producer, and most of its oil comes from Andalusia. Extreme heat during flowering and long droughts cut output sharply."}],
        "exposed": [{"name": "Deoleo (Bertolli, Carbonell)", "ticker": None, "kind": "company", "relationship": "Large olive oil bottler; buys bulk oil at market prices."},
                    {"name": "Other cooking oils", "ticker": None, "kind": "commodity", "relationship": "Shoppers switch to sunflower and canola when olive oil is dear."}],
        "outlook": "Prices follow each winter's harvest in Spain.",
        "shopper_tip": "Larger tins cost much less per ounce and keep well in the dark.",
    },
    "chocolate": {
        "headline": "West Africa's cocoa crop fell short",
        "direction": "up",
        "change_summary": "Cocoa prices hit records after poor harvests in the two countries that grow most of it.",
        "drivers": [{"title": "Disease and bad weather", "category": "weather", "location_name": "Côte d'Ivoire",
                     "lat": 6.8, "lng": -5.3,
                     "explanation": "Côte d'Ivoire and Ghana grow more than half of the world's cocoa. Heavy rain, then drought, plus swollen shoot virus cut yields from ageing trees."}],
        "exposed": [{"name": "Hershey", "ticker": "HSY", "kind": "company", "relationship": "Cocoa is its main raw material."},
                    {"name": "Mondelez", "ticker": "MDLZ", "kind": "company", "relationship": "Large chocolate maker with the same input cost."},
                    {"name": "Cocoa futures", "ticker": None, "kind": "commodity", "relationship": "The benchmark chocolate makers buy against."}],
        "outlook": "New cocoa trees need several years before they produce.",
        "shopper_tip": "Baking chocolate and store brands rose less than name-brand bars.",
    },
}


def why_for(item: str) -> dict:
    low = item.lower()
    for key, data in _WHY.items():
        if key in low:
            return {**data, "item": item, "sample": True,
                    "plain_summary": data["change_summary"]}
    return {
        "item": item, "sample": True, "headline": "No sample explanation for this item",
        "direction": "flat", "change_summary": "Add a Nimble key to research any item live.",
        "drivers": [], "exposed": [], "outlook": "", "shopper_tip": "",
        "plain_summary": "Sample explanations cover eggs, coffee, beef, orange juice, olive oil and chocolate. With a Nimble key, the Price Detective agent researches any item and cites its sources.",
    }


SAMPLE_NEWS = [
    {"title": "Sample: Container rates climb on Asia to US routes", "source": None, "url": None, "place": "Port of Los Angeles",
     "lat": 33.74, "lng": -118.26, "category": "shipping", "groceries": ["coffee", "rice"],
     "wallet_impact": "Higher freight costs can reach imported goods within a few months."},
    {"title": "Sample: Dry spell threatens coffee flowering in Brazil", "source": None, "url": None, "place": "Minas Gerais, Brazil",
     "lat": -18.5, "lng": -44.6, "category": "food", "groceries": ["coffee"],
     "wallet_impact": "A weaker crop outlook tends to lift wholesale coffee prices."},
    {"title": "Sample: Crude oil slips as supply builds", "source": None, "url": None, "place": "Cushing, Oklahoma",
     "lat": 35.98, "lng": -96.77, "category": "energy", "groceries": [],
     "wallet_impact": "Cheaper fuel lowers transport costs for most groceries over time."},
    {"title": "Sample: New avian flu detections in commercial flocks", "source": None, "url": None, "place": "Iowa, United States",
     "lat": 42.0, "lng": -93.5, "category": "food", "groceries": ["eggs", "chicken"],
     "wallet_impact": "Flock losses usually show up in egg prices within weeks."},
    {"title": "Sample: Central bank holds interest rates steady", "source": None, "url": None, "place": "Washington, DC",
     "lat": 38.89, "lng": -77.04, "category": "markets", "groceries": [],
     "wallet_impact": "Borrowing costs for cards and loans stay where they are for now."},
]

SAMPLE_RECEIPT = {
    "store": "Sample Corner Market", "date": None, "total": 43.11,
    "lines": [
        {"raw": "LRG EGGS 12CT", "item": "large eggs 12 count", "quantity": 1, "price_paid": 4.99},
        {"raw": "WHL MILK GAL", "item": "whole milk 1 gallon", "quantity": 1, "price_paid": 4.49},
        {"raw": "GRND COFFEE 12OZ", "item": "ground coffee 12 oz", "quantity": 1, "price_paid": 9.49},
        {"raw": "GRND BEEF 80/20 1LB", "item": "ground beef 1 lb", "quantity": 1, "price_paid": 7.99},
        {"raw": "OJ 52OZ", "item": "orange juice 52 fl oz", "quantity": 1, "price_paid": 5.29},
        {"raw": "EVOO 16.9OZ", "item": "extra virgin olive oil 16.9 fl oz", "quantity": 1, "price_paid": 10.86},
    ],
}

SAMPLE_SIGNALS = {
    "sample": True,
    "signals": [
        {"signal": "Open job postings", "reading": "Sample reading", "direction": "unclear",
         "why_it_matters": "Hiring on a company's careers page can hint at expansion or cuts before it is announced."},
        {"signal": "Shelf price changes", "reading": "Sample reading", "direction": "unclear",
         "why_it_matters": "Price moves on a company's own products show how much cost it is passing on."},
    ],
}


def trace_for(headline: dict, home: dict) -> dict:
    """A sample chain for a sample headline: its place, a wholesale market, the shopper's town."""
    place = headline.get("place") or "United States"
    chain = [
        {"stage": "trigger", "what": f"Sample link: the event in the headline happens here.", "place": place,
         "lat": headline.get("lat"), "lng": headline.get("lng")},
        {"stage": "market", "what": "Sample link: wholesale prices react on the commodity exchange.",
         "place": "Chicago, United States", "lat": 41.88, "lng": -87.63},
        {"stage": "shelf", "what": "Sample link: stores reprice the affected items over the following weeks.",
         "place": home["label"], "lat": home["lat"], "lng": home["lng"]},
    ]
    return {
        "sample": True, "claim": headline.get("title") or "", "verdict": "unclear",
        "verdict_reason": "Sample headline, so there is nothing to check. With a Nimble key the News Tracer agent looks for independent and official sources.",
        "primary_source": None, "chain": chain, "affected_groceries": headline.get("groceries") or [],
        "what_to_watch": "", "check": {"sites": [], "independent_sites": 0, "official_sites": 0, "original_was_read": False},
        "trust": None, "seconds": 0, "effort": "sample",
    }


_SEASONS = {
    "winter": [("oranges", "Citrus peaks in winter"), ("grapefruit", "Citrus peaks in winter"), ("cabbage", "Stores well through winter"),
               ("sweet potatoes", "Harvested in fall, sold all winter"), ("kale", "Cold-hardy green")],
    "spring": [("asparagus", "Short spring season"), ("strawberries", "First berries of the year"), ("spinach", "Cool-weather green"),
               ("peas", "Spring crop"), ("radishes", "Quick spring crop")],
    "summer": [("tomatoes", "Peak summer crop"), ("sweet corn", "Peak summer crop"), ("peaches", "Stone fruit season"),
               ("watermelon", "Peak summer crop"), ("zucchini", "Abundant in summer")],
    "fall": [("apples", "Main harvest is in fall"), ("pumpkins", "Fall harvest"), ("pears", "Fall harvest"),
             ("butternut squash", "Fall harvest"), ("sweet potatoes", "Fall harvest"), ("cranberries", "Harvested in autumn")],
}


def season_for(month: int) -> dict:
    name = "winter" if month in (12, 1, 2) else "spring" if month in (3, 4, 5) else "summer" if month in (6, 7, 8) else "fall"
    return {"sample": True, "season": name, "answer": "", "citations": [],
            "items": [{"name": n, "why_now": w, "list_name": n} for n, w in _SEASONS[name]]}


SAMPLE_DECK = {
    "sample": True,
    "cards": [
        {"term": "Inflation", "front": "What does 2% inflation actually mean?",
         "back": "Prices across a typical basket of goods are, on average, 2% higher than a year ago. Your money buys slightly less than it did.",
         "example": "Sample card: a $100 grocery run a year ago costs about $102 today.", "source": None,
         "quiz_question": "If inflation is 3%, a $50 basket from last year now costs about:", "quiz_options": ["$48.50", "$51.50", "$53.00"], "quiz_answer": 1},
        {"term": "Unit price", "front": "Why look at the small print on a shelf tag?",
         "back": "The unit price is the cost per ounce, pound or count. It lets you compare different pack sizes fairly.",
         "example": "Sample card: an 18-count carton can cost more on the shelf but less per egg.", "source": None,
         "quiz_question": "Which is cheaper per egg?", "quiz_options": ["12 eggs for $3.00", "18 eggs for $4.14", "They are equal"], "quiz_answer": 1},
        {"term": "Tariff", "front": "Who pays a tariff?",
         "back": "A tariff is a tax on imported goods, paid by the importer at the border. Importers often pass some of it on in higher prices.",
         "example": "Sample card: the US imports nearly all its coffee, so coffee tariffs reach the shelf.", "source": None,
         "quiz_question": "A tariff is collected from:", "quiz_options": ["The foreign farmer", "The importing company", "The shopper at checkout"], "quiz_answer": 1},
        {"term": "Futures contract", "front": "How can coffee have a price before it is harvested?",
         "back": "A futures contract is an agreement to buy or sell a set amount at a set price on a future date. Roasters use them to lock in costs.",
         "example": "Sample card: bad weather in Brazil moves coffee futures months before shelf prices change.", "source": None,
         "quiz_question": "A roaster buys coffee futures mainly to:", "quiz_options": ["Lock in a cost", "Avoid paying tax", "Own a farm"], "quiz_answer": 0},
        {"term": "Supply shock", "front": "Why can one bad season raise prices for years?",
         "back": "A supply shock is a sudden drop in how much can be produced. If rebuilding takes time, as with cattle herds or orchards, prices stay high until supply recovers.",
         "example": "Sample card: a rancher who keeps a calf today adds beef to stores about two years later.", "source": None,
         "quiz_question": "Prices stay high after a supply shock until:", "quiz_options": ["Demand rises", "Supply recovers", "Stores run sales"], "quiz_answer": 1},
    ],
}


def lesson_for(headline: dict, home: dict) -> dict:
    """A sample lesson, so the lesson screen can be shown without keys."""
    place = headline.get("place") or "Minas Gerais, Brazil"
    return {
        "sample": True,
        "what_happened": "Sample lesson. A dry spell hit a major coffee-growing region while the trees were flowering.",
        "why_it_matters": "Fewer flowers means fewer beans next year. Coffee is bought months ahead, so prices can move long before the shelf does.",
        "chain": [
            {"what": "Sample step: dry weather damages the coffee flowers.", "place": place,
             "lat": headline.get("lat", -18.5), "lng": headline.get("lng", -44.6)},
            {"what": "Sample step: traders expect a smaller crop, and the price of coffee contracts rises.",
             "place": "New York, United States", "lat": 40.71, "lng": -74.01},
            {"what": "Sample step: roasters pay more for beans and raise shelf prices some months later.",
             "place": home["label"], "lat": home["lat"], "lng": home["lng"]},
        ],
        "affected": [
            {"name": "Coffee roasters", "ticker": None, "kind": "sector", "effect": "costs rise",
             "because": "Sample: green coffee is their main ingredient."},
            {"name": "Coffee growers outside the dry region", "ticker": None, "kind": "sector", "effect": "sales rise",
             "because": "Sample: they sell the same crop at a higher price."},
        ],
        "expected": [{"who": "Sample forecaster", "expects": "With a Nimble key, this section lists what named agencies and analysts have published, with their numbers and dates.", "by_when": None}],
        "could_change": ["Sample: good rain in the next few weeks could let the trees recover."],
        "what_to_watch": "Sample: the next official crop estimate.",
        "concepts": [
            {"term": "Supply shock", "meaning": "A sudden drop in how much of something can be produced."},
            {"term": "Futures contract", "meaning": "An agreement to buy or sell a set amount at a set price on a later date."},
            {"term": "Input cost", "meaning": "What a company pays for the materials it turns into its product."},
        ],
        "quiz": {"question": "Sample question: why can the price of coffee rise months before the shelf price does?",
                 "options": ["Stores guess at random", "Buyers agree prices for future deliveries today", "Coffee spoils quickly"],
                 "answer": 1, "why": "Sample: coffee is bought ahead through contracts, so expectations move the price first."},
        "trust": None, "seconds": 0, "effort": "sample",
    }


def guide_for(topic: str) -> dict:
    """A sample deep-dive guide, so the screen can be shown without keys."""
    return {
        "sample": True, "topic": topic, "title": f"Sample guide: {topic[:60]}",
        "in_one_line": "Sample: when many buyers want something that takes years to make more of, its price rises.",
        "what_it_is": "Sample text. With a Nimble key, a research agent reads official reports, company filings and news, "
                      "then writes this guide about the topic you typed, citing its sources.",
        "background": [{"when": "Sample, year 1", "what": "Demand starts to grow faster than factories can be built."},
                       {"when": "Sample, year 2", "what": "Buyers sign long contracts to secure supply."}],
        "stages": [
            {"stage": "Design", "who": "Sample design firms", "place": "Santa Clara, California", "lat": 37.35, "lng": -121.95,
             "detail": "Sample: products are designed here, then made elsewhere."},
            {"stage": "Manufacturing", "who": "Sample manufacturers", "place": "Hsinchu, Taiwan", "lat": 24.8, "lng": 120.97,
             "detail": "Sample: a few factories make most of the supply, so a delay here moves prices everywhere."},
            {"stage": "Assembly", "who": "Sample assemblers", "place": "Penang, Malaysia", "lat": 5.41, "lng": 100.33,
             "detail": "Sample: parts are packaged and tested before shipping."}],
        "happening_now": "Sample: this section describes what is happening this month, with numbers and dates.",
        "price_drivers": [
            {"driver": "Demand outrunning supply", "direction": "pushes prices up", "explanation": "Sample: new factories take years to build."},
            {"driver": "New factories opening", "direction": "pushes prices down", "explanation": "Sample: more supply eases shortages."}],
        "key_players": [{"name": "Sample company", "ticker": None, "exchange": None, "role": "Sample: what it does in this story."}],
        "numbers": [{"figure": "Sample figure", "value": "a number with its unit", "as_of": "its date"}],
        "things_to_know": ["Sample: industries like this move in cycles of shortage and glut.",
                           "Sample: a company can sell more and still earn less if its costs rise faster."],
        "expected": [{"who": "Sample forecaster", "expects": "What a named agency or analyst has published, with numbers and dates.", "by_when": None}],
        "mistakes": ["Sample: treating one headline as a trend."],
        "watch": ["Sample: the next industry sales report."],
        "quiz": [{"question": "Sample question: why can prices stay high for a long time?",
                  "options": ["Factories take years to build", "Prices never fall", "Shops choose prices at random"],
                  "answer": 0, "why": "Sample: supply cannot respond quickly."}],
        "research": [], "written_by": None, "notes": [],
        "sources": [], "trust": None, "seconds": 0, "effort": "sample",
    }
