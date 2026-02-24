CREATE TABLE "Users" (
	"user_id" INTEGER NOT NULL UNIQUE,
	"name" TEXT,
	"dob" TEXT,
	"gender" TEXT,
	"occupation" TEXT,
	"description" TEXT,
	"chronic_disease" TEXT,
	PRIMARY KEY("user_id")
);


CREATE TABLE "UserBMIRecords" (
	"record_id" INTEGER NOT NULL UNIQUE,
	"user_id" INTEGER NOT NULL,
	"date" TEXT NOT NULL UNIQUE,
	"weight" INTEGER,
	"height" INTEGER,
	PRIMARY KEY("record_id" AUTOINCREMENT),
	FOREIGN KEY("user_id") REFERENCES "Users"("user_id")
	UNIQUE("user_id", "date")
);


CREATE TABLE "UserSummaryRecords" (
	"record_id"	INTEGER NOT NULL UNIQUE,
	"user_id"	INTEGER NOT NULL,
	"date"	INTEGER NOT NULL,
	"overview" TEXT,
	"office_risk" TEXT,
	"office_summary" TEXT,
	PRIMARY KEY("record_id" AUTOINCREMENT),
	FOREIGN KEY("user_id") REFERENCES "Users"("user_id")
	UNIQUE("user_id", "date")
);


CREATE TABLE "UserActivityRecords" (
    "record_id" INTEGER NOT NULL UNIQUE,
    "user_id"   INTEGER NOT NULL,
    "date"      TEXT NOT NULL, -- Use ISO8601 string "YYYY-MM-DD"
    "steps"     INTEGER DEFAULT 0,
    "calories_burned" REAL,
    "avg_heart_rate"  INTEGER,
    "active_minutes"  INTEGER,
    "sleep_hours"     REAL,
    "source_device"   TEXT, -- e.g., "Apple Watch", "Fitbit"
    PRIMARY KEY("record_id" AUTOINCREMENT),
    FOREIGN KEY("user_id") REFERENCES "Users"("user_id"),
	UNIQUE(user_id, date)
);

CREATE TABLE "MessageMappings" (
	message_id INTEGER PRIMARY KEY,
	user_id INTEGER NOT NULL,
	timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_id ON MessageMappings(user_id)
