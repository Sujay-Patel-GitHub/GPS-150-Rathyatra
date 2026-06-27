async function run() {
  const bbox = "23.025,72.544,23.029,72.549";
  const query = `[out:json];
(
  node[name](${bbox});
  way[name](${bbox});
);
out body;
>;
out skel qt;`;

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
      console.log(`Found ${data.elements.length} elements with names:`);
      data.elements.forEach(el => {
        if (el.tags && el.tags.name) {
          const coords = el.lat && el.lon ? `${el.lat}, ${el.lon}` : (el.center ? `${el.center.lat}, ${el.center.lon}` : 'no coords');
          console.log(`- [${el.type}] ${el.tags.name} (${coords}) [Tags: ${JSON.stringify(el.tags)}]`);
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
