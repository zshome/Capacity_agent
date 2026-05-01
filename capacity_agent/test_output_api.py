import urllib.request
import json

print('Testing Output APIs on port 8006...')

# Health check
try:
    r = urllib.request.urlopen('http://localhost:8006/health')
    print('Health:', r.read().decode())
except Exception as e:
    print('Health error:', e)

# Test Output RCCP
try:
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
    r = urllib.request.urlopen(req, timeout=10)
    result = json.loads(r.read().decode())
    print('\n=== Output RCCP Result ===')
    print('Perspective:', result.get('metadata', {}).get('perspective'))
    print('Predicted output:', result.get('total_predicted_output'))
    print('Output gap:', result.get('output_gap'))
    print('Feasible:', result.get('feasible'))
    print('Risk summary:', result.get('risk_summary'))
except Exception as e:
    print('Output RCCP error:', e)

# Test Input Plan
try:
    data = json.dumps({
        'output_targets': {
            '2026-W20': {'28nm_DRAM_A': 500},
            '2026-W21': {'28nm_DRAM_A': 600}
        },
        'wip_predictions': {
            '2026-W20': {'28nm_DRAM_A': 300}
        },
        'current_week': '2026-W17',
        'planning_weeks': 12
    }).encode()
    
    req = urllib.request.Request('http://localhost:8006/input/plan',
                                  data=data,
                                  headers={'Content-Type': 'application/json'})
    r = urllib.request.urlopen(req, timeout=10)
    result = json.loads(r.read().decode())
    print('\n=== Input Plan Result ===')
    print('Total input needed:', result.get('total_input_needed'))
    print('Feasible:', result.get('feasible'))
    print('Late targets:', result.get('late_targets'))
except Exception as e:
    print('Input Plan error:', e)