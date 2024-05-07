import { Example } from "./Example";

import styles from "./Example.module.css";

export type ExampleModel = {
    text: string;
    value: string;
};

const EXAMPLES: ExampleModel[] = [
    {
        text: "How do I change my bank account details?",
        value: "How do I change my bank account details?"
    },
    { text: "How do I get help with domestic and family violence?", value: "How do I get help with domestic and family violence?" },
    {
        text: "Does the Council offer employees learning and development opportunities?",
        value: "Does the Council offer employees learning and development opportunities?"
    }
];

interface Props {
    onExampleClicked: (value: string) => void;
}

export const ExampleList = ({ onExampleClicked }: Props) => {
    return (
        <ul className={styles.examplesNavList}>
            {EXAMPLES.map((x, i) => (
                <li key={i}>
                    <Example text={x.text} value={x.value} onClick={onExampleClicked} />
                </li>
            ))}
        </ul>
    );
};
