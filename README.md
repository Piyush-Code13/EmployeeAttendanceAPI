# Employee Attendance and Analytics API

## About the project

This project is a REST API built using Python, FastAPI, and MongoDB to manage employee attendance and generate attendance reports.

It allows employees to punch in and out, helps HR correct attendance records, and provides reports about attendance, late arrivals, overtime, and department performance.

## Features

- Create and view employee records.
- Record employee punch-in and punch-out times.
- Calculate late minutes, work hours, overtime, and half-day status.
- Correct attendance records while keeping a history of changes.
- Generate monthly attendance reports for employees.
- View department-wise attendance summaries.
- Show a leaderboard of employees with late arrivals, including tied rankings.
- View daily department attendance trends.
- Check API health and inspect MongoDB query execution plans.
- Validate inputs and handle duplicate requests.

## Technologies used

- Python
- FastAPI
- MongoDB
- PyMongo
- Pydantic

## How to run the project

### 1. Install Python dependencies

Open a terminal in the project folder and run:

```bash
pip install -r requirements.txt
```

### 2. Configure MongoDB

Make sure MongoDB is running and available.

Set the following environment variables if you use a custom MongoDB connection:

- `MONGO_URI` — MongoDB connection URI
- `MONGO_DB` — database name

The application uses default local MongoDB settings if these variables are not provided.

### 3. Start the API

Run this command from the project root:

```bash
python -m uvicorn app.main:app --reload --port 8000
```

### 4. Open the API documentation

Once the server starts, open:

http://127.0.0.1:8000/docs

You can use Swagger UI to try the available API endpoints.

To check whether the API is running, open:

http://127.0.0.1:8000/health

## Notes

- Attendance dates and shift times follow Indian Standard Time (IST).
- API timestamps use epoch milliseconds.
- MongoDB stores attendance timestamps as dates.
- Attendance corrections are saved in the history of each record.
- The API uses MongoDB indexes to support data retrieval and concurrent updates.

## Testing

I tested important cases during development, including pagination validation, attendance calculations, overnight shifts, concurrent punch-in and punch-out requests, regularization history, leaderboard ties, department summaries, and daily attendance trends.

