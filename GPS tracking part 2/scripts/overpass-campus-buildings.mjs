async function run() {
  const bbox = "23.025,72.544,23.029,72.549";
  const query = `[out:json];
(
  node[building](${bbox});
  way[building](${bbox});
  node[amenity](${bbox});
  way[amenity](${bbox});
);
out center;`;

  const url = `https://overpass-api.de/api/interpreter?data=${encodeURIComponent(query)}`;
  try {
    const res = await fetch(url, {
      method: "GET",
      headers: {
        "User-Agent": "RathYatraGPSTrackerTest/1.0",
        "Accept": "application/json"
      }
    });

    if (res.ok) {
      const data = await res.json();
      console.log(`Found ${data.elements.length} elements:`);
      data.elements.forEach(el => {
        if (el.tags) {
          const lat = el.lat || (el.center ? el.center.lat : null);
          const lon = el.lon || (el.center ? el.center.lon : null);
          console.log(`- [${el.type}] ${el.tags.name || el.tags.building || el.tags.amenity || 'unnamed'} (${lat}, ${lon}) [Tags: ${JSON.stringify(el.tags)}]`);
        }
      });
    } else {
      console.log(`HTTP Error: ${res.status}`);
    }
  } catch (e) {
    console.error("Overpass query failed:", e);
  }
}

run();
