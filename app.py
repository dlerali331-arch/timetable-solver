import os
from flask import Flask, request, jsonify
from ortools.sat.python import cp_model

app = Flask(__name__)

@app.route('/solve', methods=['POST'])
def solve_timetable():
    try:
        data = request.json
        days = data.get('days', [])
        periods = data.get('periods', [])
        workload = data.get('workload', [])
        constraints = data.get('constraints', {})
        matrix = data.get('matrix', {})

        model = cp_model.CpModel()
        
        # Variables
        X = {}
        for lesson in workload:
            for d in days:
                for p in periods:
                    X[(lesson['id'], d, p)] = model.NewBoolVar(f"x_{lesson['id']}_{d}_{p}")

        # 1. Lesson assignment
        for lesson in workload:
            model.AddExactlyOne(X[(lesson['id'], d, p)] for d in days for p in periods)

        # 2. Class conflict
        for d in days:
            for p in periods:
                for class_code in set(l['classCode'] for l in workload):
                    class_lessons = [l['id'] for l in workload if l['classCode'] == class_code]
                    model.Add(sum(X[(l_id, d, p)] for l_id in class_lessons) <= 1)

        # 3. Teacher conflict
        for d in days:
            for p in periods:
                for teacher in set(l['teacher'] for l in workload):
                    t_lessons = [l['id'] for l in workload if l['teacher'] == teacher]
                    model.Add(sum(X[(l_id, d, p)] for l_id in t_lessons) <= 1)

        # 4. Strict Constraints
        for teacher, c in constraints.items():
            t_lessons = [l['id'] for l in workload if l['teacher'] == teacher]
            max_per_day = c.get('maxPerDay', len(periods))
            
            for d in days:
                model.Add(sum(X[(l_id, d, p)] for l_id in t_lessons for p in periods) <= max_per_day)
                if teacher in matrix and d in matrix[teacher]:
                    for un_p in matrix[teacher][d]:
                        for l_id in t_lessons:
                            model.Add(X[(l_id, d, un_p)] == 0)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 15.0
        status = solver.Solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            result = []
            for lesson in workload:
                for d in days:
                    for p in periods:
                        if solver.Value(X[(lesson['id'], d, p)]) == 1:
                            result.append({
                                "id": lesson['id'],
                                "day": d,
                                "period": p,
                                "classCode": lesson['classCode'],
                                "subject": lesson['subject'],
                                "teacher": teacher if 'teacher' in lesson else lesson['teacher']
                            })
            return jsonify({"status": "SUCCESS", "schedule": result})
        else:
            return jsonify({"status": "FAILED", "message": "No solution found"})
    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)