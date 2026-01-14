import json
from canvas_api import get_user_todo

def main():
    data = get_user_todo()

    # Pretty print to terminal
    print(json.dumps(data, indent=2))

    # Save to file for inspection
    with open("raw_todo.json", "w") as f:
        json.dump(data, f, indent=2)

    print("\nSaved raw Canvas response to raw_todo.json")

if __name__ == "__main__":
    main()
