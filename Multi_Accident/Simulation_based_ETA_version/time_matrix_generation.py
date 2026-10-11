import json

with open('/home/nkk/work/src/python/Multi_Accident/Simulation_based_ETA_version/case1/eta_res.json', 'r', encoding='utf-8') as f:
    eta_res = json.load(f)
    
with open('/home/nkk/work/src/python/Multi_Accident/Simulation_based_ETA_version/case1/location_input.json', 'r', encoding='utf-8') as f:
    location_input = json.load(f)
    
hospitals = location_input['hospitals']
accidents = location_input['accidents']
time_matrix = {}
for hos in hospitals:
    time_matrix[f"Hospital {hos}"] = {}
for hos in hospitals:
    for acc in accidents:
        time_matrix[f"Hospital {hos}"][f"Accident {acc}"] = 9999

for hos in hospitals:
    for acc in accidents:
        for temp_res in eta_res:
            if temp_res['start_road'] == hos and temp_res['end_road'] == acc:
                if temp_res['eta_seconds']:
                    temp_go = temp_res['eta_seconds']
                else:
                    temp_go = 9999
            if temp_res['start_road'] == acc and temp_res['end_road'] == hos:
                if temp_res['eta_seconds']:
                    temp_back = temp_res['eta_seconds']
                else:
                    temp_back = 9999
        temp_total = temp_go + temp_back
        if temp_total < 9999:
            print(f"From {hos} to {acc}: {temp_go} seconds, back: {temp_back} seconds, total: {temp_total} seconds")
            time_matrix[f"Hospital {hos}"][f"Accident {acc}"] = temp_total
            
print(time_matrix)
with open('/home/nkk/work/src/python/Multi_Accident/Simulation_based_ETA_version/case1/time_matrix.json', 'w', encoding='utf-8') as f:
    json.dump(time_matrix, f, ensure_ascii=False, indent=2)