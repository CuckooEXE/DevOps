#include <gtest/gtest.h>

#include "mylib.h"

TEST(MyLib, Add) { EXPECT_EQ(mylib_add(2, 3), 5); }

TEST(MyLib, Greeting) {
  EXPECT_STREQ(mylib_greeting(), "hello from MyCoolLib");
}
