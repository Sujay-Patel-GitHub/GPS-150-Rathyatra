async function run() {
  const bbox = "23.025,72.544,23.029,72.549";
  const query = `[out:json];
(
  node[amenity=canteen](${bbox});
  way[amenity=canteen](${bbox});
  node[amenity=cafe](${bbox});
  way[amenity=cafe](${bbox});
  node[amenity=fast_food](${bbox});
  way[amenity=fast_food](${bbox});
  node[amenity=restaurant](${bbox});
  way[amenity=restaurant](${bbox});
);
out body;
>;
out skel qt;`;

  const url = `https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`;
  try {
    console.log("Querying Overpass API for food amenities inside GP Ahmedabad...");
    const res = await fetch(url, {
      method: "GET",
      headers: {
        "User-Agent": "RathYatraGPSTrackerTest/1.0",
        "Accept": "application/json"
      }
    });

    if (res.ok) {
      const data = await res.json();
      console.log(`Success! Found ${data.elements.length} elements:`);
      data.elements.forEach(el => {
        console.log(`- Type: ${el.type}, ID: ${el.id}`);
        if (el.tags) {
          console.log(`  Tags:`, JSON.stringify(el.tags));
        }
        if (el.lat && el.lon) {
          console.log(`  Coords: ${el.lat}, ${el.lon}`);
        } else if (el.center) {
          console.log(`  Center: ${el.center.lat}, ${el.center.lon}`);
        }
      });
    } else {
      console.log(`HTTP Error: ${res.status}`);
      const text = await res.text();
      console.log(text);
    }
  } catch (e) {
    console.error("Overpass query failed:", e);
  }
}

run();
