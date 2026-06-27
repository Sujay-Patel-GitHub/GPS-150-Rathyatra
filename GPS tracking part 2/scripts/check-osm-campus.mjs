async function run() {
  // Bounding box: south, west, north, east
  const bbox = "23.025,72.545,23.028,72.548";
  const query = `[out:json][timeout:15];way(${bbox});out geom;`;
  
  const servers = [
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter"
  ];

  let success = false;
  for (const server of servers) {
    try {
      console.log(`Querying ${server}...`);
      const res = await fetch(server, {
        method: "POST",
        body: `data=${encodeURIComponent(query)}`,
        headers: { "Content-Type": "application/x-www-form-urlencoded" }
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      console.log(`OSM elements found: ${data.elements?.length || 0}`);
      
      if (data.elements && data.elements.length > 0) {
        data.elements.forEach((el, idx) => {
          if (el.tags && (el.tags.highway || el.tags.name || el.tags.service)) {
            console.log(`\nElement ${idx + 1}:`);
            console.log(`  ID: ${el.id}`);
            console.log(`  Tags:`, el.tags);
            if (el.geometry) {
              console.log(`  Points: ${el.geometry.length}`);
            }
          }
        });
        success = true;
        break;
      }
    } catch (e) {
      console.warn(`Failed:`, e.message);
    }
  }
}

run();
