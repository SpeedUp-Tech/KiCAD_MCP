/**
 * elk_layout_runner.cjs - Step 2: Run ELK Layout Engine
 * 
 * Takes an ELK graph JSON input and produces a layouted graph with
 * calculated positions for all nodes and edges.
 * 
 * Usage:
 *   node elk_layout_runner.cjs [input.json] [output.json]
 * 
 * If no arguments provided, defaults to elk_input.json / elk_output.json
 * in the project root.
 */

const ELK = require('elkjs');
const fs = require('fs');
const path = require('path');

const elk = new ELK();

// Parse command line arguments
const args = process.argv.slice(2);
const projectRoot = path.resolve(__dirname, '../../..');

const inputPath = args[0] 
  ? path.resolve(args[0]) 
  : path.join(projectRoot, 'elk_input.json');
  
const outputPath = args[1] 
  ? path.resolve(args[1]) 
  : path.join(projectRoot, 'elk_output.json');

// Validate input file exists
if (!fs.existsSync(inputPath)) {
  console.error(`Error: Input file not found: ${inputPath}`);
  process.exit(1);
}

// Read and parse input
const graph = JSON.parse(fs.readFileSync(inputPath, 'utf8'));

// Run ELK layout
console.log(`Running ELK layout...`);
console.log(`  Input:  ${inputPath}`);
console.log(`  Output: ${outputPath}`);

elk.layout(graph)
  .then(data => {
    // Save result to file
    fs.writeFileSync(outputPath, JSON.stringify(data, null, 2));
    console.log(`Layout complete. Saved to ${outputPath}`);
  })
  .catch(err => {
    console.error("ELK Layout Failed:", err);
    process.exit(1);
  });
