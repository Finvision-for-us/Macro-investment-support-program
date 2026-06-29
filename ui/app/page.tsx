import { redirect } from "next/navigation";

// 루트는 곧장 /today 로. Day 9~10 에서 /today 가 채워진다.
export default function Home() {
  redirect("/today");
}
