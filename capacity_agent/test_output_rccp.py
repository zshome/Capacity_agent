import urllib.request
import json

# Test Output RCCP
data = json.dumps({
    'output_target': {'28nm_DRAM_A': 100},
    'output_target_week': '2026-W17',
    'wip_lot_detail': [
        {'lot_id': 'L001', 'product_id': '28nm_DRAM_A', 'current_step_seq': 230, 
         'wafer_count': 25, 'percent_complete': 85, 'lot_status': 'WAIT', 
         'current_tool_group': 'ETCH_01', 'wait_hours_so_far': 12}
    ],
    'available_hours': {'ETCH_01': 500, 'LITHO_01': 600, 'DEPO_01': 400}
}).encode()

req = urllib.request.Request('http://localhost:8006/output/rccp/compute',
                              data=data,
                              headers={'Content-Type': 'application/json'})
r = urllib.request.urlopen(req, timeout=30)
result = json.loads(r.read().decode())

print('=== Output RCCP Result ===')
print('Perspective:', result.get('metadata', {}).get('perspective'))
print('Predicted output:', result.get('total_predicted_output'))
print('Output gap:', result.get('output_gap'))
print('Feasible:', result.get('feasible'))