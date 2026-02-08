CREATE TABLE "Users" (
	"userID" INTEGER NOT NULL UNIQUE,
	"name" TEXT,
	"dob" TEXT,
	"occupation" TEXT,
	"description" TEXT,
	"chronic_disease" TEXT,
	PRIMARY KEY("userID")
);


CREATE TABLE "UserBMIRecords" (
	"recordID" INTEGER NOT NULL UNIQUE,
	"datetime" TEXT NOT NULL UNIQUE,
	"userID" INTEGER NOT NULL,
	"weight" INTEGER,
	"height" INTEGER,
	PRIMARY KEY("recordID" AUTOINCREMENT),
	FOREIGN KEY("userID") REFERENCES "Users"("userID")
);
