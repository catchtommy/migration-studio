import { parseOracleConnect } from "./oracleConnect";

test("connection strings pasted into Host are split into host, port and service", () => {
  expect(parseOracleConnect("dbserver.example.com")).toBeNull();
  expect(parseOracleConnect("jdbc:oracle:thin:@//dbserver.example.com:1521/ORCLPDB1")).toEqual({ host: "dbserver.example.com", port: 1521, service: "ORCLPDB1" });
  expect(parseOracleConnect("jdbc:oracle:thin:@dbserver:1522:ORCL")).toEqual({ host: "dbserver", port: 1522, service: "ORCL" });
  expect(parseOracleConnect("jdbc:oracle:thin:scott/tiger@//10.1.2.3:1521/APP")).toEqual({ host: "10.1.2.3", port: 1521, service: "APP" });
  expect(parseOracleConnect("dbserver/SVC")).toEqual({ host: "dbserver", port: null, service: "SVC" });
  expect(parseOracleConnect("(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=db.corp)(PORT=1530))(CONNECT_DATA=(SERVICE_NAME=PDB1)))")).toEqual({
    host: "db.corp",
    port: 1530,
    service: "PDB1",
  });
});
